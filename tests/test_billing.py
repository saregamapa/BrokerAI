"""S1-02 / S1-04: Integration tests for Stripe billing endpoints.

Tests cover:
  - GET /billing/plans — public plan catalogue
  - GET /me/plan — authenticated plan/usage snapshot
  - POST /billing/checkout — gate (missing Stripe key), happy-path stub
  - POST /billing/portal — gate (no subscription), happy-path stub
  - POST /billing/webhook — signature guard
  - HTTP 402 enforcement: campaign cap, platform cap, feature gates
  - Webhook handler logic (offline, no real Stripe)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from backend.db import engine
from backend.models import Campaign, User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register(client: TestClient, email: str | None = None, password: str = "Password1!") -> dict:
    email = email or f"bill_{secrets.token_hex(5)}@example.com"
    r = client.post("/signup", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    # Resolve user_id via /me (signup doesn't return it directly)
    me = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    return {"email": email, "password": password, "user_id": me.json()["id"]}


def _login(client: TestClient, creds: dict) -> str:
    r = client.post("/login", json={"email": creds["email"], "password": creds["password"]})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _set_plan(user_id: int, plan: str, sub_id: str | None = None) -> None:
    with Session(engine) as s:
        u = s.get(User, user_id)
        assert u is not None
        u.plan = plan
        if sub_id:
            u.stripe_subscription_id = sub_id
            u.stripe_customer_id = "cus_test_" + secrets.token_hex(4)
        s.add(u)
        s.commit()


def _add_campaigns(user_id: int, count: int) -> None:
    with Session(engine) as s:
        for i in range(count):
            s.add(Campaign(
                user_id=user_id,
                name=f"Billing test campaign {i}",
                objective="Test",
                target_audience="All",
                status="generated",
                created_at=datetime.utcnow(),
            ))
        s.commit()


# ---------------------------------------------------------------------------
# GET /billing/plans
# ---------------------------------------------------------------------------

def _get_plans(client: TestClient) -> list:
    """Return the plans list, handling both bare-list and wrapped {plans: [...]} responses."""
    r = client.get("/billing/plans")
    assert r.status_code == 200
    data = r.json()
    return data if isinstance(data, list) else data.get("plans", data)


class TestGetPlans:
    def test_returns_four_plans(self, client):
        plans = _get_plans(client)
        assert isinstance(plans, list)
        assert len(plans) == 4

    def test_plan_slugs(self, client):
        slugs = [p["plan"] for p in _get_plans(client)]
        assert slugs == ["starter", "growth", "pro", "scale"]

    def test_plan_prices(self, client):
        plans = {p["plan"]: p for p in _get_plans(client)}
        assert plans["starter"]["price_usd"] == 29
        assert plans["growth"]["price_usd"] == 79
        assert plans["pro"]["price_usd"] == 259
        assert plans["scale"]["price_usd"] == 399

    def test_growth_is_highlighted(self, client):
        plans = {p["plan"]: p for p in _get_plans(client)}
        assert plans["growth"]["highlighted"] is True
        assert plans["starter"]["highlighted"] is False
        assert plans["pro"]["highlighted"] is False
        assert plans["scale"]["highlighted"] is False

    def test_plan_has_required_fields(self, client):
        plans = _get_plans(client)
        required = {"plan", "name", "price_usd", "price_period", "tagline",
                    "campaigns_per_month", "posts_per_month", "platforms",
                    "team_seats", "videos_per_month", "analytics_ai", "comment_automations"}
        for p in plans:
            assert required.issubset(set(p.keys())), f"Missing fields in {p['plan']}"

    def test_unauthenticated_access_allowed(self, client):
        """Plans endpoint must be public (no auth required)."""
        r = client.get("/billing/plans")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /me/plan
# ---------------------------------------------------------------------------

class TestGetMyPlan:
    def test_requires_auth(self, client):
        r = client.get("/me/plan")
        assert r.status_code == 401

    def test_new_user_defaults_to_starter(self, client):
        creds = _register(client)
        token = _login(client, creds)
        r = client.get("/me/plan", headers=_auth(token))
        assert r.status_code == 200
        data = r.json()
        assert data["plan"] == "starter"
        assert data["limits"]["campaigns_per_month"] == 1
        assert data["limits"]["analytics_ai"] is True
        assert data["limits"]["custom_brand_kit"] is True
        assert data["usage"]["campaigns_this_month"] == 0

    def test_usage_reflects_current_campaigns(self, client):
        creds = _register(client)
        token = _login(client, creds)
        uid = creds["user_id"]
        _add_campaigns(uid, 2)
        r = client.get("/me/plan", headers=_auth(token))
        assert r.status_code == 200
        assert r.json()["usage"]["campaigns_this_month"] == 2

    def test_starter_plan_reflected(self, client):
        creds = _register(client)
        token = _login(client, creds)
        _set_plan(creds["user_id"], "starter")
        r = client.get("/me/plan", headers=_auth(token))
        assert r.status_code == 200
        data = r.json()
        assert data["plan"] == "starter"
        assert data["limits"]["campaigns_per_month"] == 1
        assert data["limits"]["platforms_allowed"] == 2

    def test_growth_plan_features(self, client):
        creds = _register(client)
        token = _login(client, creds)
        _set_plan(creds["user_id"], "growth")
        data = client.get("/me/plan", headers=_auth(token)).json()
        assert data["limits"]["analytics_ai"] is True
        assert data["limits"]["comment_automations"] is False
        assert data["limits"]["team_seats"] == 1

    def test_scale_plan_reflected(self, client):
        creds = _register(client)
        token = _login(client, creds)
        _set_plan(creds["user_id"], "scale")
        data = client.get("/me/plan", headers=_auth(token)).json()
        assert data["plan"] == "scale"
        assert data["limits"]["campaigns_per_month"] == 20
        assert data["limits"]["platforms_allowed"] == 15
        assert data["limits"]["team_seats"] == 5
        assert data["limits"]["comment_automations"] is True

    def test_pro_plan_reflected(self, client):
        creds = _register(client)
        token = _login(client, creds)
        _set_plan(creds["user_id"], "pro")
        data = client.get("/me/plan", headers=_auth(token)).json()
        assert data["plan"] == "pro"
        assert data["limits"]["campaigns_per_month"] == 10
        assert data["limits"]["team_seats"] == 3
        assert data["limits"]["comment_automations"] is True

    def test_plan_expires_at_returned(self, client):
        creds = _register(client)
        token = _login(client, creds)
        exp = datetime.utcnow() + timedelta(days=3)
        with Session(engine) as s:
            u = s.get(User, creds["user_id"])
            u.plan_expires_at = exp
            s.add(u)
            s.commit()
        data = client.get("/me/plan", headers=_auth(token)).json()
        assert data["plan_expires_at"] is not None

    def test_upgrade_to_populated_for_non_top_tier(self, client):
        creds = _register(client)
        token = _login(client, creds)
        data = client.get("/me/plan", headers=_auth(token)).json()
        # New users default to starter; next upgrade is growth
        assert data["upgrade_to"] == "growth"
        assert data["upgrade_price_usd"] == 79

    def test_upgrade_to_none_for_agency(self, client):
        creds = _register(client)
        token = _login(client, creds)
        _set_plan(creds["user_id"], "agency")
        data = client.get("/me/plan", headers=_auth(token)).json()
        assert data["upgrade_to"] is None


# ---------------------------------------------------------------------------
# POST /billing/checkout — gating (no real Stripe needed)
# ---------------------------------------------------------------------------

class TestCheckoutGating:
    def test_requires_auth(self, client):
        r = client.post("/billing/checkout", json={"plan": "starter"})
        assert r.status_code == 401

    def test_invalid_plan_rejected(self, client):
        creds = _register(client)
        token = _login(client, creds)
        r = client.post("/billing/checkout", json={"plan": "bogus_plan"}, headers=_auth(token))
        assert r.status_code in (400, 422)

    def test_missing_stripe_key_returns_503(self, client):
        """Without STRIPE_SECRET_KEY set, checkout must fail gracefully (not 500)."""
        creds = _register(client)
        token = _login(client, creds)
        import os
        original = os.environ.pop("STRIPE_SECRET_KEY", None)
        try:
            r = client.post("/billing/checkout", json={"plan": "starter"}, headers=_auth(token))
            # Acceptable: 503 (stripe not configured) or 400 (price placeholder)
            assert r.status_code in (400, 503), r.text
        finally:
            if original is not None:
                os.environ["STRIPE_SECRET_KEY"] = original

    def test_checkout_with_mocked_stripe(self, client, monkeypatch):
        """Happy-path: starter user upgrades to growth — mocked Stripe returns a URL."""
        from backend.core.plan_limits import PLAN_GROWTH, PlanLimits
        creds = _register(client)
        token = _login(client, creds)
        # New users are on starter; upgrade to growth
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_mock_key")

        # Patch get_limits so growth has a real price ID (bypasses placeholder guard)
        real_growth_with_price = PlanLimits(
            plan="growth",
            campaigns_per_month=5,
            posts_per_campaign=30,
            platforms_allowed=5,
            team_seats=1,
            videos_per_month=5,
            analytics_ai=True,
            comment_automations=False,
            custom_brand_kit=True,
            stripe_price_id_monthly="price_growth_test_001",
            monthly_price_usd=79,
        )
        monkeypatch.setattr(
            "backend.services.billing_service.get_limits",
            lambda plan: real_growth_with_price if plan == "growth" else PLAN_GROWTH,
        )

        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/pay/cs_test_mock"
        mock_session.id = "cs_test_mock"

        with patch("backend.services.billing_service._stripe") as mock_stripe_fn:
            mock_stripe = MagicMock()
            mock_stripe.checkout.Session.create.return_value = mock_session
            mock_stripe_fn.return_value = mock_stripe
            r = client.post("/billing/checkout", json={"plan": "growth"}, headers=_auth(token))

        assert r.status_code == 200, r.text
        data = r.json()
        assert "url" in data
        assert "checkout.stripe.com" in data["url"]


# ---------------------------------------------------------------------------
# POST /billing/portal — gating
# ---------------------------------------------------------------------------

class TestPortalGating:
    def test_requires_auth(self, client):
        r = client.post("/billing/portal")
        assert r.status_code == 401

    def test_no_subscription_returns_400(self, client):
        creds = _register(client)
        token = _login(client, creds)
        # User has no stripe_customer_id or subscription
        r = client.post("/billing/portal", headers=_auth(token))
        assert r.status_code == 400

    def test_portal_with_mocked_stripe(self, client, monkeypatch):
        """Happy-path: mocked Stripe portal session."""
        creds = _register(client)
        token = _login(client, creds)
        _set_plan(creds["user_id"], "starter", sub_id="sub_test_001")

        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_mock_key")

        mock_portal = MagicMock()
        mock_portal.url = "https://billing.stripe.com/p/session/test_mock"

        with patch("backend.services.billing_service._stripe") as mock_stripe_fn:
            mock_stripe = MagicMock()
            mock_stripe.billing_portal.Session.create.return_value = mock_portal
            mock_stripe_fn.return_value = mock_stripe
            r = client.post("/billing/portal", headers=_auth(token))

        assert r.status_code == 200, r.text
        assert "billing.stripe.com" in r.json()["url"]


# ---------------------------------------------------------------------------
# POST /billing/webhook — signature guard
# ---------------------------------------------------------------------------

class TestWebhookSignatureGuard:
    def test_missing_signature_returns_400(self, client):
        r = client.post(
            "/billing/webhook",
            content=b'{"type":"checkout.session.completed"}',
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 400

    def test_bad_signature_returns_400(self, client, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_key")
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_secret")
        r = client.post(
            "/billing/webhook",
            content=b'{"type":"checkout.session.completed"}',
            headers={
                "content-type": "application/json",
                "stripe-signature": "t=0,v1=badhash",
            },
        )
        assert r.status_code == 400

    def test_valid_webhook_handled(self, client, monkeypatch):
        """Construct a valid Stripe webhook signature and send it."""
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_key")
        secret = "whsec_testsecret1234"
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)

        # Build a minimal checkout.session.completed payload
        creds = _register(client)
        payload_obj = {
            "id": "evt_test_001",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_001",
                    "customer": "cus_test_001",
                    "subscription": "sub_test_001",
                    "metadata": {
                        "user_id": str(creds["user_id"]),
                        "plan": "starter",
                    },
                }
            },
        }
        payload_bytes = json.dumps(payload_obj).encode()
        ts = str(int(time.time()))
        signed_payload = f"{ts}.".encode() + payload_bytes
        sig = hmac.new(
            secret.encode(),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()
        stripe_sig = f"t={ts},v1={sig}"

        with patch("backend.services.billing_service._stripe") as mock_stripe_fn:
            mock_stripe = MagicMock()

            # Stripe Webhook.construct_event should return a dict-like event
            mock_event = {
                "id": "evt_test_001",
                "type": "checkout.session.completed",
                "data": {
                    "object": payload_obj["data"]["object"],
                },
            }
            mock_stripe.Webhook.construct_event.return_value = mock_event
            mock_stripe_fn.return_value = mock_stripe

            r = client.post(
                "/billing/webhook",
                content=payload_bytes,
                headers={
                    "content-type": "application/json",
                    "stripe-signature": stripe_sig,
                },
            )

        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# HTTP 402 enforcement — campaign cap
# ---------------------------------------------------------------------------

def _connect_social(user_id: int) -> None:
    """Mark user as socially connected so /generate-campaign doesn't 400."""
    with Session(engine) as s:
        u = s.get(User, user_id)
        u.ayrshare_profile_key = "pytest-ayrshare-profile-key"
        u.social_connected = True
        s.add(u)
        s.commit()


class TestCampaignCapEnforcement:
    def test_starter_campaign_limit_is_enforced_at_1(self, client, monkeypatch):
        """Starter plan blocks on the 2nd campaign (cap = 1/month)."""
        creds = _register(client)
        token = _login(client, creds)
        uid = creds["user_id"]
        _set_plan(uid, "starter")
        _add_campaigns(uid, 1)  # exactly at cap
        _connect_social(uid)

        from tests.helpers import stub_run_campaign_phase1
        monkeypatch.setattr(
            "backend.main.run_campaign_phase1",
            stub_run_campaign_phase1,
            raising=False,
        )

        r = client.post(
            "/generate-campaign",
            json={
                "goal": "Sell more homes",
                "location": "Test City",
                "platforms": ["instagram"],
                "tone": "professional",
            },
            headers=_auth(token),
        )
        assert r.status_code == 402, r.text
        assert r.json()["detail"]["limit_key"] == "campaigns_per_month"

    def test_scale_campaign_limit_is_enforced_at_20(self, client, monkeypatch):
        """Scale plan blocks on the 21st campaign (cap = 20/month)."""
        creds = _register(client)
        token = _login(client, creds)
        uid = creds["user_id"]
        _set_plan(uid, "scale")
        _add_campaigns(uid, 20)  # exactly at cap
        _connect_social(uid)

        from tests.helpers import stub_run_campaign_phase1
        monkeypatch.setattr(
            "backend.main.run_campaign_phase1",
            stub_run_campaign_phase1,
            raising=False,
        )

        r = client.post(
            "/generate-campaign",
            json={
                "goal": "Scale up listings",
                "location": "Test City",
                "platforms": ["instagram"],
                "tone": "professional",
            },
            headers=_auth(token),
        )
        assert r.status_code == 402, r.text
        assert r.json()["detail"]["limit_key"] == "campaigns_per_month"


# ---------------------------------------------------------------------------
# HTTP 402 enforcement — platform cap
# ---------------------------------------------------------------------------

class TestPlatformCapEnforcement:
    def test_starter_user_blocked_on_three_platforms(self, client, monkeypatch):
        """Starter plan allows 2 platforms; 3 platforms must return 402."""
        creds = _register(client)
        token = _login(client, creds)
        uid = creds["user_id"]
        _set_plan(uid, "starter")
        _connect_social(uid)

        from tests.helpers import stub_run_campaign_phase1
        monkeypatch.setattr(
            "backend.main.run_campaign_phase1",
            stub_run_campaign_phase1,
            raising=False,
        )

        r = client.post(
            "/generate-campaign",
            json={
                "goal": "Grow my business",
                "location": "Test City",
                "platforms": ["instagram", "linkedin", "twitter"],
                "tone": "professional",
            },
            headers=_auth(token),
        )
        assert r.status_code == 402, r.text
        detail = r.json()["detail"]
        assert detail["limit_key"] == "platforms_allowed"
        assert detail["upgrade_required"] is True


# ---------------------------------------------------------------------------
# HTTP 402 enforcement — feature gates
# ---------------------------------------------------------------------------

class TestFeatureGates:
    def test_analytics_ai_allowed_for_starter(self, client):
        """Starter is the minimum plan; analytics_ai must be enabled."""
        creds = _register(client)
        token = _login(client, creds)
        # Endpoint may 200/404/503 depending on data — but must NOT gate with 402
        r = client.get("/analytics/insights", headers=_auth(token))
        assert r.status_code != 402, "Starter user should not hit analytics_ai gate"

    def test_analytics_ai_allowed_for_growth(self, client, monkeypatch):
        creds = _register(client)
        token = _login(client, creds)
        _set_plan(creds["user_id"], "growth")
        # The endpoint may 404/200/503 depending on campaign data — but NOT 402
        r = client.get("/analytics/insights", headers=_auth(token))
        assert r.status_code != 402, "Growth user should not hit analytics_ai gate"
