"""
Invite flow test suite.

Covers:
1. Owner can create an invite → invite link is generated
2. Valid invite token + correct email → user created and attached to team
3. Invalid token → signup fails with 400
4. Already-used token → signup fails with 400
5. Expired token → signup fails with 400
6. Email mismatch → signup fails with 400
7. Non-owner cannot create an invite
8. GET /invite-info with valid token → pre-fill data returned
9. GET /invite-info with invalid token → valid=False
10. Member limit enforcement → invite creation blocked at max
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.db import engine
from backend.models import Team, TeamInvite, User


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _signup(
    client: TestClient,
    email: str,
    account_type: str = "individual",
    team_name: str = "",
    password: str = "pass1234",
) -> str:
    body: dict = {"email": email, "password": password, "account_type": account_type}
    if team_name:
        body["team_name"] = team_name
    r = client.post("/signup", json=body)
    assert r.status_code == 200, f"signup failed: {r.text}"
    return r.json()["access_token"]


def _signup_with_invite(
    client: TestClient,
    email: str,
    invite_token: str,
    password: str = "pass1234",
) -> tuple[int, dict]:
    """Returns (status_code, json_body)."""
    r = client.post(
        "/signup",
        json={"email": email, "password": password, "invite_token": invite_token},
    )
    try:
        body = r.json()
    except Exception:
        body = {}
    return r.status_code, body


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _me(client: TestClient, token: str) -> dict:
    r = client.get("/me", headers=_auth(token))
    assert r.status_code == 200
    return r.json()


def _create_invite(
    client: TestClient,
    owner_token: str,
    team_id: int,
    email: str,
) -> tuple[int, dict]:
    r = client.post(
        f"/teams/{team_id}/invite",
        json={"email": email},
        headers=_auth(owner_token),
    )
    try:
        body = r.json()
    except Exception:
        body = {}
    return r.status_code, body


def _extract_token_from_url(signup_url: str) -> str:
    """Pull invite_token query param from the URL returned by the API."""
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(signup_url)
    params = parse_qs(parsed.query)
    tokens = params.get("invite_token", [])
    assert tokens, f"No invite_token in signup_url: {signup_url}"
    return tokens[0]


def _expire_invite(token_str: str) -> None:
    """Directly expire a token in the DB for testing."""
    with Session(engine) as s:
        invite = s.exec(
            select(TeamInvite).where(TeamInvite.token == token_str)
        ).first()
        assert invite is not None, "Token not found in DB"
        invite.expires_at = datetime.utcnow() - timedelta(hours=1)
        s.add(invite)
        s.commit()


# ────────────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────────────

class TestInviteCreation:
    """Owner can create an invite; link is generated; non-owner is blocked."""

    def test_owner_can_create_invite(self, client: TestClient):
        owner_tok = _signup(client, "inv_owner1@x.com", "team", "InvTest Team A")
        owner_me = _me(client, owner_tok)
        team_id = owner_me["team_id"]

        status, body = _create_invite(client, owner_tok, team_id, "inv_invitee1@x.com")
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert "token" in body, "Response must include invite token"
        assert body["email"] == "inv_invitee1@x.com"
        assert body["team_id"] == team_id
        assert body["is_used"] is False

    def test_invite_signup_url_contains_token(self, client: TestClient):
        owner_tok = _signup(client, "inv_owner2@x.com", "team", "InvTest Team B")
        team_id = _me(client, owner_tok)["team_id"]

        status, body = _create_invite(client, owner_tok, team_id, "inv_urlcheck@x.com")
        assert status == 200
        assert "signup_url" in body, "Response must include signup_url"
        token_from_url = _extract_token_from_url(body["signup_url"])
        assert token_from_url == body["token"], "Token in URL must match response token"

    def test_non_owner_member_cannot_create_invite(self, client: TestClient):
        # Owner signs up and invites a member
        owner_tok = _signup(client, "inv_owner3@x.com", "team", "InvTest Team C")
        owner_me = _me(client, owner_tok)
        team_id = owner_me["team_id"]

        _, invite_body = _create_invite(client, owner_tok, team_id, "inv_member1@x.com")
        inv_tok = invite_body["token"]

        # Member signs up via invite
        _, mb = _signup_with_invite(client, "inv_member1@x.com", inv_tok)
        member_tok = mb["access_token"]

        # Member tries to create an invite — should be forbidden
        status, body = _create_invite(client, member_tok, team_id, "inv_anothermember@x.com")
        assert status == 403, f"Expected 403, got {status}: {body}"


class TestInviteSignup:
    """Valid invite → user created and attached to team."""

    def test_valid_invite_creates_user_in_team(self, client: TestClient):
        owner_tok = _signup(client, "inv_owner4@x.com", "team", "InvTest Team D")
        owner_me = _me(client, owner_tok)
        team_id = owner_me["team_id"]

        _, invite_body = _create_invite(client, owner_tok, team_id, "inv_new1@x.com")
        inv_tok = invite_body["token"]

        status, body = _signup_with_invite(client, "inv_new1@x.com", inv_tok)
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert "access_token" in body

        # Verify user is attached to the team
        member_tok = body["access_token"]
        me = _me(client, member_tok)
        assert me["team_id"] == team_id
        assert me["role"] == "member"

    def test_invite_token_is_single_use(self, client: TestClient):
        owner_tok = _signup(client, "inv_owner5@x.com", "team", "InvTest Team E")
        team_id = _me(client, owner_tok)["team_id"]

        _, invite_body = _create_invite(client, owner_tok, team_id, "inv_singleuse@x.com")
        inv_tok = invite_body["token"]

        # First use — should succeed
        status1, _ = _signup_with_invite(client, "inv_singleuse@x.com", inv_tok)
        assert status1 == 200

        # Second use (same token) — should fail
        status2, body2 = _signup_with_invite(client, "inv_singleuse2@x.com", inv_tok)
        assert status2 == 400, f"Expected 400 on second use, got {status2}: {body2}"


class TestInviteValidation:
    """Invalid token / used token / expired token / email mismatch → 400."""

    def test_invalid_token_rejected(self, client: TestClient):
        status, body = _signup_with_invite(
            client, "inv_bad@x.com", "completely-invalid-token-xyz-abc"
        )
        assert status == 400, f"Expected 400, got {status}: {body}"
        assert "invalid" in (body.get("detail") or "").lower()

    def test_used_token_rejected(self, client: TestClient):
        owner_tok = _signup(client, "inv_owner6@x.com", "team", "InvTest Team F")
        team_id = _me(client, owner_tok)["team_id"]

        _, invite_body = _create_invite(client, owner_tok, team_id, "inv_used@x.com")
        inv_tok = invite_body["token"]

        # Redeem once
        s1, _ = _signup_with_invite(client, "inv_used@x.com", inv_tok)
        assert s1 == 200

        # Try again with a different email (token is used)
        status, body = _signup_with_invite(client, "inv_used2@x.com", inv_tok)
        assert status == 400, f"Expected 400 (used), got {status}: {body}"
        assert "used" in (body.get("detail") or "").lower()

    def test_expired_token_rejected(self, client: TestClient):
        owner_tok = _signup(client, "inv_owner7@x.com", "team", "InvTest Team G")
        team_id = _me(client, owner_tok)["team_id"]

        _, invite_body = _create_invite(client, owner_tok, team_id, "inv_expired@x.com")
        inv_tok = invite_body["token"]

        # Manually expire the token in the DB
        _expire_invite(inv_tok)

        status, body = _signup_with_invite(client, "inv_expired@x.com", inv_tok)
        assert status == 400, f"Expected 400 (expired), got {status}: {body}"
        assert "expir" in (body.get("detail") or "").lower()

    def test_email_mismatch_rejected(self, client: TestClient):
        owner_tok = _signup(client, "inv_owner8@x.com", "team", "InvTest Team H")
        team_id = _me(client, owner_tok)["team_id"]

        _, invite_body = _create_invite(client, owner_tok, team_id, "inv_correct@x.com")
        inv_tok = invite_body["token"]

        # Sign up with WRONG email
        status, body = _signup_with_invite(client, "inv_wrong@x.com", inv_tok)
        assert status == 400, f"Expected 400 (email mismatch), got {status}: {body}"
        assert "email" in (body.get("detail") or "").lower()


class TestInviteInfo:
    """GET /invite-info?token=XYZ endpoint for frontend pre-fill."""

    def test_valid_token_returns_email(self, client: TestClient):
        owner_tok = _signup(client, "inv_owner9@x.com", "team", "InvTest Team I")
        team_id = _me(client, owner_tok)["team_id"]

        _, invite_body = _create_invite(client, owner_tok, team_id, "inv_info@x.com")
        inv_tok = invite_body["token"]

        r = client.get(f"/invite-info?token={inv_tok}")
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is True
        assert data["email"] == "inv_info@x.com"
        assert data["team_id"] == team_id

    def test_invalid_token_returns_valid_false(self, client: TestClient):
        r = client.get("/invite-info?token=totally-bogus-token")
        assert r.status_code == 200  # endpoint always returns 200
        data = r.json()
        assert data["valid"] is False
        assert "error" in data


class TestInviteMemberLimit:
    """Member limit is enforced when creating invites."""

    def test_invite_blocked_when_team_is_full(self, client: TestClient):
        # Team type = "team" → max_members = 5
        owner_tok = _signup(client, "inv_limit_owner@x.com", "team", "InvTest Full Team")
        owner_me = _me(client, owner_tok)
        team_id = owner_me["team_id"]

        # Owner already occupies 1 slot. Fill remaining 4 slots via invite + signup.
        for i in range(4):
            invitee_email = f"inv_limit_member{i}@x.com"
            _, ib = _create_invite(client, owner_tok, team_id, invitee_email)
            s, _ = _signup_with_invite(client, invitee_email, ib["token"])
            assert s == 200, f"Member {i} signup failed"

        # Now team is full (5/5). The 6th invite must be rejected.
        status, body = _create_invite(client, owner_tok, team_id, "inv_limit_overflow@x.com")
        assert status == 422, f"Expected 422 (limit reached), got {status}: {body}"
        assert "limit" in (body.get("detail") or "").lower()
