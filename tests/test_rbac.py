"""
RBAC test suite — permission matrix unit tests only.

HTTP integration tests that depended on signup/invite flows were removed
along with user authentication.
"""
from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from backend.models import Team, User
from backend.permissions import check_permission
from backend.services.team_service import effective_team_role


def _make_user(account_type: str, role: str) -> User:
    u = User(email="x@x.com", password_hash="x")
    u.account_type = account_type
    u.role = role
    return u


class TestPermissionUnit:
    def test_individual_owner_all_allowed(self):
        u = _make_user("individual", "owner")
        for action in [
            "create_campaign",
            "edit_campaign",
            "review_campaign",
            "approve_campaign",
            "publish_campaign",
        ]:
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


class TestEffectiveTeamRole:
    """When user.role is blank in DB, infer from Team.owner_id."""

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
