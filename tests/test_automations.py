"""S5-06: Tests for /automations CRUD endpoints and simulate logic."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _signup_and_token(client: TestClient, email: str, password: str = "Secret123!") -> str:
    r = client.post("/signup", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAutomationCRUD:
    def test_create_automation(self, client: TestClient) -> None:
        token = _signup_and_token(client, "auto_create@example.com")
        r = client.post(
            "/automations",
            json={
                "name": "Welcome rule",
                "trigger_keywords": "hello,hi,interested",
                "reply_template": "Thanks {{name}}! DM us for details.",
                "is_active": True,
            },
            headers=_auth(token),
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["name"] == "Welcome rule"
        assert data["trigger_keywords"] == "hello,hi,interested"
        assert data["reply_template"] == "Thanks {{name}}! DM us for details."
        assert data["is_active"] is True
        assert data["match_count"] == 0
        assert "id" in data

    def test_list_automations(self, client: TestClient) -> None:
        token = _signup_and_token(client, "auto_list@example.com")
        # Create two rules
        client.post(
            "/automations",
            json={"name": "Rule A", "trigger_keywords": "buy", "reply_template": "Thanks!"},
            headers=_auth(token),
        )
        client.post(
            "/automations",
            json={"name": "Rule B", "trigger_keywords": "sell", "reply_template": "Great!"},
            headers=_auth(token),
        )
        r = client.get("/automations", headers=_auth(token))
        assert r.status_code == 200, r.text
        data = r.json()
        assert "automations" in data
        assert "total" in data
        assert data["total"] == 2
        names = {a["name"] for a in data["automations"]}
        assert "Rule A" in names
        assert "Rule B" in names

    def test_get_automation(self, client: TestClient) -> None:
        token = _signup_and_token(client, "auto_get@example.com")
        create_r = client.post(
            "/automations",
            json={"name": "Get me", "trigger_keywords": "test", "reply_template": "Hi!"},
            headers=_auth(token),
        )
        assert create_r.status_code == 201
        rule_id = create_r.json()["id"]

        r = client.get(f"/automations/{rule_id}", headers=_auth(token))
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Get me"

    def test_update_automation(self, client: TestClient) -> None:
        token = _signup_and_token(client, "auto_update@example.com")
        create_r = client.post(
            "/automations",
            json={"name": "Old name", "trigger_keywords": "old", "reply_template": "Old reply"},
            headers=_auth(token),
        )
        rule_id = create_r.json()["id"]

        r = client.put(
            f"/automations/{rule_id}",
            json={"name": "New name", "trigger_keywords": "new,updated"},
            headers=_auth(token),
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["name"] == "New name"
        assert data["trigger_keywords"] == "new,updated"

    def test_delete_automation(self, client: TestClient) -> None:
        token = _signup_and_token(client, "auto_delete@example.com")
        create_r = client.post(
            "/automations",
            json={"name": "Delete me", "trigger_keywords": "gone", "reply_template": "Bye"},
            headers=_auth(token),
        )
        rule_id = create_r.json()["id"]

        r = client.delete(f"/automations/{rule_id}", headers=_auth(token))
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert r.json()["deleted_id"] == rule_id

        # Should now 404
        r2 = client.get(f"/automations/{rule_id}", headers=_auth(token))
        assert r2.status_code == 404

    def test_toggle_automation(self, client: TestClient) -> None:
        token = _signup_and_token(client, "auto_toggle@example.com")
        create_r = client.post(
            "/automations",
            json={"name": "Toggle me", "trigger_keywords": "on", "reply_template": "Hi", "is_active": True},
            headers=_auth(token),
        )
        rule_id = create_r.json()["id"]
        assert create_r.json()["is_active"] is True

        # Toggle off
        r = client.patch(f"/automations/{rule_id}/toggle", headers=_auth(token))
        assert r.status_code == 200, r.text
        assert r.json()["is_active"] is False

        # Toggle back on
        r2 = client.patch(f"/automations/{rule_id}/toggle", headers=_auth(token))
        assert r2.status_code == 200
        assert r2.json()["is_active"] is True

    def test_simulate_triggered(self, client: TestClient) -> None:
        token = _signup_and_token(client, "auto_simulate_yes@example.com")
        create_r = client.post(
            "/automations",
            json={
                "name": "Sim rule",
                "trigger_keywords": "hello,hi,interested",
                "reply_template": "Thanks {{name}}! DM us for details.",
            },
            headers=_auth(token),
        )
        rule_id = create_r.json()["id"]

        r = client.post(
            f"/automations/{rule_id}/simulate",
            json={"test_comment": "Hi, I'm interested in this property!"},
            headers=_auth(token),
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["triggered"] is True
        # Should match either 'hi' or 'interested'
        assert data["matched_keyword"] in ("hi", "hello", "interested")
        # {{name}} replaced with empty string
        assert "{{name}}" not in data["reply_preview"]

    def test_simulate_not_triggered(self, client: TestClient) -> None:
        token = _signup_and_token(client, "auto_simulate_no@example.com")
        create_r = client.post(
            "/automations",
            json={
                "name": "Sim rule 2",
                "trigger_keywords": "buy,purchase",
                "reply_template": "Thanks!",
            },
            headers=_auth(token),
        )
        rule_id = create_r.json()["id"]

        r = client.post(
            f"/automations/{rule_id}/simulate",
            json={"test_comment": "Just looking around, no intent."},
            headers=_auth(token),
        )
        assert r.status_code == 200, r.text
        assert r.json()["triggered"] is False
        assert r.json()["matched_keyword"] == ""

    def test_ownership_403_on_get(self, client: TestClient) -> None:
        """Another user cannot read someone else's automation."""
        token_a = _signup_and_token(client, "auto_own_a@example.com")
        token_b = _signup_and_token(client, "auto_own_b@example.com")

        create_r = client.post(
            "/automations",
            json={"name": "Private", "trigger_keywords": "secret", "reply_template": "Nope"},
            headers=_auth(token_a),
        )
        rule_id = create_r.json()["id"]

        r = client.get(f"/automations/{rule_id}", headers=_auth(token_b))
        assert r.status_code == 403

    def test_ownership_403_on_delete(self, client: TestClient) -> None:
        token_a = _signup_and_token(client, "auto_del_a@example.com")
        token_b = _signup_and_token(client, "auto_del_b@example.com")

        create_r = client.post(
            "/automations",
            json={"name": "Mine", "trigger_keywords": "x", "reply_template": "y"},
            headers=_auth(token_a),
        )
        rule_id = create_r.json()["id"]

        r = client.delete(f"/automations/{rule_id}", headers=_auth(token_b))
        assert r.status_code == 403

    def test_list_filter_by_campaign_id(self, client: TestClient) -> None:
        """Filter automations by campaign_id query param (uses a fake campaign_id)."""
        from sqlmodel import Session
        from backend.db import engine
        from backend.models import Campaign
        from datetime import datetime

        token = _signup_and_token(client, "auto_filter_camp@example.com")

        # Get user id from /me
        me_r = client.get("/me", headers=_auth(token))
        user_id = me_r.json()["id"]

        # Insert campaign directly via DB
        with Session(engine) as s:
            camp = Campaign(
                user_id=user_id,
                name="Test Camp",
                objective="Test",
                target_audience="All",
                status="draft",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            s.add(camp)
            s.commit()
            s.refresh(camp)
            camp_id = camp.id

        # Rule linked to campaign
        client.post(
            "/automations",
            json={"name": "Camp rule", "trigger_keywords": "buy", "reply_template": "Hi", "campaign_id": camp_id},
            headers=_auth(token),
        )
        # Rule without campaign
        client.post(
            "/automations",
            json={"name": "No camp rule", "trigger_keywords": "sell", "reply_template": "Hi"},
            headers=_auth(token),
        )

        r = client.get(f"/automations?campaign_id={camp_id}", headers=_auth(token))
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["automations"][0]["name"] == "Camp rule"

    def test_unauthenticated_returns_401(self, client: TestClient) -> None:
        r = client.get("/automations")
        assert r.status_code == 401

    def test_get_nonexistent_returns_404(self, client: TestClient) -> None:
        token = _signup_and_token(client, "auto_404@example.com")
        r = client.get("/automations/99999999", headers=_auth(token))
        assert r.status_code == 404
