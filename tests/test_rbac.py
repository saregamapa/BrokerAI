"""
RBAC test suite.

Covers:
- individual: full access (all actions allowed)
- team member: create/edit allowed; approve/publish blocked
- org admin: approve/publish allowed
- team member limit enforcement
- org admin cap enforcement
- team owner-only role promotion
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sqlmodel import Session, SQLModel, create_engine

from backend.models import Team, User
from backend.permissions import check_permission
from backend.services.team_service import effective_team_role


# ────────────────────────────────────────────────────────────────────────────
# Unit tests — check_permission()
# ────────────────────────────────────────────────────────────────────────────

def _make_user(account_type: str, role: str) -> User:
    u = User(email="x@x.com", password_hash="x")
    u.account_type = account_type
    u.role = role
    return u


class TestPermissionUnit:
    def test_individual_owner_all_allowed(self):
        u = _make_user("individual", "owner")
        for action in ["create_campaign", "edit_campaign", "review_campaign", "approve_campaign", "publish_campaign"]:
            assert check_permission(u, action), f"individual/owner should be allowed: {action}"

    def test_team_owner_all_allowed(self):
        u = _make_user("team", "owner")
        for action in ["create_campaign", "approve_campaign", "publish_campaign"]:
            assert check_permission(u, action)

    def test_team_member_cannot_approve(self):
        u = _make_user("team", "member")
        assert check_permission(u, "create_campaign")
        assert check_permission(u, "edit_campaign")
        assert check_permission(u, "review_campaign")
        assert not check_permission(u, "approve_campaign")
        assert not check_permission(u, "publish_campaign")

    def test_org_admin_can_approve_and_publish(self):
        u = _make_user("org", "admin")
        assert check_permission(u, "approve_campaign")
        assert check_permission(u, "publish_campaign")

    def test_org_member_cannot_approve(self):
        u = _make_user("org", "member")
        assert not check_permission(u, "approve_campaign")
        assert not check_permission(u, "publish_campaign")
        assert check_permission(u, "create_campaign")


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _signup(client: TestClient, email: str, account_type: str = "individual", team_name: str = "") -> str:
    body = {"email": email, "password": "pass1234", "account_type": account_type}
    if team_name:
        body["team_name"] = team_name
    r = client.post("/signup", json=body)
    assert r.status_code == 200, f"signup failed: {r.text}"
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _me(client: TestClient, token: str) -> dict:
    r = client.get("/me", headers=_auth(token))
    assert r.status_code == 200
    return r.json()


def _invite_and_join(
    client: TestClient,
    owner_token: str,
    team_id: int,
    email: str,
    password: str = "pass1234",
) -> tuple[str, int]:
    """
    Full invite flow helper: create invite → signup with token.
    Returns (member_access_token, member_user_id).
    """
    # Create invite and get token
    ir = client.post(
        f"/teams/{team_id}/invite",
        json={"email": email},
        headers=_auth(owner_token),
    )
    assert ir.status_code == 200, f"invite creation failed: {ir.text}"
    invite_token = ir.json()["token"]

    # Signup with invite token
    sr = client.post(
        "/signup",
        json={"email": email, "password": password, "invite_token": invite_token},
    )
    assert sr.status_code == 200, f"invite signup failed: {sr.text}"
    member_tok = sr.json()["access_token"]
    member_id = _me(client, member_tok)["id"]
    return member_tok, member_id


# ────────────────────────────────────────────────────────────────────────────
# Signup — account_type propagated correctly
# ────────────────────────────────────────────────────────────────────────────

class TestSignupAccountType:
    def test_individual_default(self, client: TestClient):
        tok = _signup(client, "rbac_ind1@x.com", "individual")
        me = _me(client, tok)
        assert me["account_type"] == "individual"
        assert me["role"] == "owner"
        assert me["team_id"] is None

    def test_team_signup_creates_team(self, client: TestClient):
        tok = _signup(client, "rbac_team1@x.com", "team", "Acme Realty")
        me = _me(client, tok)
        assert me["account_type"] == "team"
        assert me["role"] == "owner"
        assert me["team_id"] is not None

    def test_org_signup_creates_team(self, client: TestClient):
        tok = _signup(client, "rbac_org1@x.com", "org", "Big Corp")
        me = _me(client, tok)
        assert me["account_type"] == "org"
        assert me["role"] == "owner"
        assert me["team_id"] is not None

    def test_invalid_account_type_rejected(self, client: TestClient):
        r = client.post("/signup", json={
            "email": "rbac_bad@x.com", "password": "pass1234", "account_type": "superadmin"
        })
        assert r.status_code == 422


# ────────────────────────────────────────────────────────────────────────────
# Team management: invite, list, update role
# ────────────────────────────────────────────────────────────────────────────

class TestTeamManagement:
    def test_invite_and_list_members(self, client: TestClient):
        owner_tok = _signup(client, "rbac_tm_owner1@x.com", "team", "My Team")
        owner_me = _me(client, owner_tok)
        team_id = owner_me["team_id"]

        # Invite + join via token flow
        member_tok, _ = _invite_and_join(client, owner_tok, team_id, "rbac_tm_invitee1@x.com")

        # Verify the joined member's profile
        mem_me = _me(client, member_tok)
        assert mem_me["role"] == "member"
        assert mem_me["team_id"] == team_id

        # List
        r2 = client.get(f"/teams/{team_id}/members", headers=_auth(owner_tok))
        assert r2.status_code == 200
        emails = [m["email"] for m in r2.json()]
        assert "rbac_tm_owner1@x.com" in emails
        assert "rbac_tm_invitee1@x.com" in emails

    def test_member_cannot_invite(self, client: TestClient):
        owner_tok = _signup(client, "rbac_tm_owner2@x.com", "team", "Another Team")
        owner_me = _me(client, owner_tok)
        team_id = owner_me["team_id"]

        # Member joins via invite token flow
        m1_tok, _ = _invite_and_join(client, owner_tok, team_id, "rbac_tm_m1@x.com")

        # Actual team member tries to invite another — must be blocked
        r = client.post(f"/teams/{team_id}/invite", json={"email": "rbac_tm_m2@x.com"}, headers=_auth(m1_tok))
        assert r.status_code == 403

    def test_owner_updates_member_to_admin(self, client: TestClient):
        owner_tok = _signup(client, "rbac_role_owner@x.com", "team", "Role Team")
        team_id = _me(client, owner_tok)["team_id"]
        _, member_id = _invite_and_join(client, owner_tok, team_id, "rbac_role_m@x.com")

        r = client.put(f"/teams/{team_id}/members/{member_id}", json={"role": "admin"}, headers=_auth(owner_tok))
        assert r.status_code == 200
        assert r.json()["role"] == "admin"

    def test_member_cannot_assign_admin(self, client: TestClient):
        owner_tok = _signup(client, "rbac_admin_owner@x.com", "team", "Admin Test Team")
        team_id = _me(client, owner_tok)["team_id"]
        m1_tok, _ = _invite_and_join(client, owner_tok, team_id, "rbac_admin_m1@x.com")
        _, m2_id = _invite_and_join(client, owner_tok, team_id, "rbac_admin_m2@x.com")

        r = client.put(f"/teams/{team_id}/members/{m2_id}", json={"role": "admin"}, headers=_auth(m1_tok))
        assert r.status_code == 403

    def test_team_member_limit_enforced(self, client: TestClient):
        """Team max_members=5; owner + 4 members = 5 total. 6th invite should fail."""
        owner_tok = _signup(client, "rbac_limit_owner@x.com", "team", "Limit Team")
        team_id = _me(client, owner_tok)["team_id"]

        for i in range(4):
            email = f"rbac_limit_m{i}@x.com"
            _invite_and_join(client, owner_tok, team_id, email)

        # 6th invite (exceeds limit)
        r = client.post(f"/teams/{team_id}/invite", json={"email": "rbac_limit_over@x.com"}, headers=_auth(owner_tok))
        assert r.status_code == 422
        assert "limit" in r.json()["detail"].lower()

    def test_org_admin_cap_enforced(self, client: TestClient):
        """Org allows max 3 admins. 4th admin assignment should fail."""
        owner_tok = _signup(client, "rbac_orgcap_owner@x.com", "org", "Cap Org")
        team_id = _me(client, owner_tok)["team_id"]

        member_ids = []
        for i in range(4):
            email = f"rbac_orgcap_m{i}@x.com"
            _, mid = _invite_and_join(client, owner_tok, team_id, email)
            member_ids.append(mid)

        # Promote first 3 to admin — should succeed
        for mid in member_ids[:3]:
            r = client.put(f"/teams/{team_id}/members/{mid}", json={"role": "admin"}, headers=_auth(owner_tok))
            assert r.status_code == 200, f"admin {mid} promotion failed: {r.text}"

        # 4th admin — should fail
        r = client.put(f"/teams/{team_id}/members/{member_ids[3]}", json={"role": "admin"}, headers=_auth(owner_tok))
        assert r.status_code == 422
        assert "admin" in r.json()["detail"].lower()


# ────────────────────────────────────────────────────────────────────────────
# Campaign flow RBAC
# ────────────────────────────────────────────────────────────────────────────

class TestCampaignPermissions:
    def test_individual_can_submit_for_review(self, client: TestClient):
        """Individual users have full permission — submit-review should work."""
        tok = _signup(client, "rbac_camp_ind@x.com", "individual")
        # Can't create real campaign without social/OpenAI in tests; just verify the 403 isn't
        # from RBAC (it would be from social-connect guard or missing campaign)
        r = client.post("/campaigns/9999/submit-review", headers=_auth(tok))
        # 404 (campaign not found) = RBAC passed, correct business-logic block
        assert r.status_code in (404, 400)

    def test_team_member_cannot_approve_campaign(self, client: TestClient):
        """Team member calling /approve-campaign should get 403 from RBAC, not 404."""
        owner_tok = _signup(client, "rbac_camp_owner@x.com", "team", "Camp Team")
        team_id = _me(client, owner_tok)["team_id"]
        member_tok, _ = _invite_and_join(client, owner_tok, team_id, "rbac_camp_member@x.com")

        r = client.post("/approve-campaign", json={"campaign_id": 9999}, headers=_auth(member_tok))
        assert r.status_code == 403
        assert "permission" in r.json()["detail"].lower()

    def test_team_owner_can_call_approve_endpoint(self, client: TestClient):
        """Team owner calling /approve-campaign gets 403 from social-connect guard, NOT from RBAC."""
        tok = _signup(client, "rbac_camp_own2@x.com", "team", "Owner Team 2")
        r = client.post("/approve-campaign", json={"campaign_id": 9999}, headers=_auth(tok))
        # 403 from social-connect guard OR 404 = RBAC passed
        assert r.status_code in (403, 404, 400)
        if r.status_code == 403:
            # Must NOT be from RBAC permission check — should be social-connect
            detail = r.json().get("detail", "")
            assert "social" in detail.lower() or "connect" in detail.lower(), \
                f"Unexpected 403 reason: {detail}"

    def test_org_admin_can_call_approve_endpoint(self, client: TestClient):
        """Org admin should pass RBAC check on /approve-campaign."""
        owner_tok = _signup(client, "rbac_orgadm_owner@x.com", "org", "Org Admin Test")
        team_id = _me(client, owner_tok)["team_id"]
        admin_tok, admin_id = _invite_and_join(client, owner_tok, team_id, "rbac_orgadm_admin@x.com")
        client.put(f"/teams/{team_id}/members/{admin_id}", json={"role": "admin"}, headers=_auth(owner_tok))

        r = client.post("/approve-campaign", json={"campaign_id": 9999}, headers=_auth(admin_tok))
        # 403 from social-connect or 404 = RBAC passed; must not be RBAC-403
        assert r.status_code in (403, 404, 400)
        if r.status_code == 403:
            detail = r.json().get("detail", "")
            assert "social" in detail.lower() or "connect" in detail.lower(), \
                f"Unexpected RBAC 403 for admin: {detail}"

    def test_org_member_cannot_approve_campaign(self, client: TestClient):
        owner_tok = _signup(client, "rbac_orgm_owner@x.com", "org", "Org Member Test")
        team_id = _me(client, owner_tok)["team_id"]
        member_tok, _ = _invite_and_join(client, owner_tok, team_id, "rbac_orgm_member@x.com")

        r = client.post("/approve-campaign", json={"campaign_id": 9999}, headers=_auth(member_tok))
        assert r.status_code == 403
        assert "permission" in r.json()["detail"].lower()


class TestEffectiveTeamRole:
    """When user.role is blank in DB, infer from Team.owner_id (invite members never show as owner)."""

    def test_infer_owner_and_member_when_role_missing(self):
        engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)
        with Session(engine) as s:
            owner = User(
                email="eff_owner@x.com",
                password_hash="x",
                account_type="team",
                role="",
            )
            s.add(owner)
            s.commit()
            s.refresh(owner)
            team = Team(name="T", account_type="team", owner_id=int(owner.id), max_members=5)
            s.add(team)
            s.commit()
            s.refresh(team)
            owner.team_id = team.id
            s.add(owner)
            s.commit()
            s.refresh(owner)
            assert effective_team_role(s, owner) == "owner"

            member = User(
                email="eff_member@x.com",
                password_hash="x",
                account_type="team",
                role="",
                team_id=team.id,
            )
            s.add(member)
            s.commit()
            s.refresh(member)
            assert effective_team_role(s, member) == "member"
