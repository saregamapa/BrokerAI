#!/usr/bin/env python3
"""Create BrokerAI demo / QA accounts (idempotent).

Run from the repository root:

  python scripts/seed_demo_accounts.py

Plan limits match ``backend/core/plan_limits.py`` (free / starter / growth / pro / agency).
Team demo: owner (agency, unlimited campaigns) + admin (agency) + member (growth).

Shared password for all seeded accounts (change after first login in production):

  BrokerAI-Demo!1

Optional: set in ``.env`` so an existing non-seed admin email skips the monthly cap:

  BROKERAI_UNLIMITED_CAMPAIGNS_EMAILS=you@company.com
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlmodel import Session

from backend.auth import get_user_by_email, hash_password
from backend.db import create_db_and_tables, engine
from backend.models import User
from backend.services.team_service import create_team, invite_member

DEMO_PASSWORD = "BrokerAI-Demo!1"
DOMAIN = "demo.brokerai.test"


def _ensure_user(
    session: Session,
    *,
    email: str,
    plan: str,
    account_type: str = "individual",
    role: str = "owner",
    team_id: int | None = None,
    email_verified: bool = True,
) -> User:
    existing = get_user_by_email(session, email)
    if existing:
        existing.plan = plan
        existing.account_type = account_type
        existing.role = role
        if team_id is not None:
            existing.team_id = team_id
        existing.password_hash = hash_password(DEMO_PASSWORD)
        existing.email_verified = email_verified
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    u = User(
        email=email,
        password_hash=hash_password(DEMO_PASSWORD),
        plan=plan,
        account_type=account_type,
        role=role,
        team_id=team_id,
        email_verified=email_verified,
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def main() -> None:
    create_db_and_tables()

    owner_email = f"owner@{DOMAIN}"
    admin_email = f"admin@{DOMAIN}"
    member_email = f"member@{DOMAIN}"

    with Session(engine) as session:
        # --- Team workspace (owner + admin + member) ---
        owner = _ensure_user(
            session,
            email=owner_email,
            plan="agency",
            account_type="team",
            role="owner",
        )
        if owner.team_id is None:
            create_team(
                session,
                name="BrokerAI Demo Team",
                account_type="team",
                owner=owner,
            )
            session.refresh(owner)

        tid = int(owner.team_id or 0)
        assert tid > 0, "team_id missing after create_team"

        _ensure_user(session, email=admin_email, plan="agency", account_type="individual", role="owner")
        _ensure_user(session, email=member_email, plan="growth", account_type="individual", role="owner")

        admin_u = get_user_by_email(session, admin_email)
        member_u = get_user_by_email(session, member_email)
        assert admin_u and member_u

        if admin_u.team_id != tid:
            invite_member(
                session,
                team_id=tid,
                inviter=owner,
                invitee_email=admin_email,
            )
            session.refresh(admin_u)
        admin_u = session.get(User, admin_u.id)
        assert admin_u is not None
        admin_u.role = "admin"
        admin_u.plan = "agency"
        session.add(admin_u)
        session.commit()

        if member_u.team_id != tid:
            invite_member(
                session,
                team_id=tid,
                inviter=owner,
                invitee_email=member_email,
            )
            session.refresh(member_u)
        member_u = session.get(User, member_u.id)
        assert member_u is not None
        member_u.role = "member"
        member_u.plan = "growth"
        session.add(member_u)
        session.commit()

        # --- Solo accounts: exact tier limits from plan_limits ---
        _ensure_user(session, email=f"free@{DOMAIN}", plan="free")
        _ensure_user(session, email=f"starter@{DOMAIN}", plan="starter")
        _ensure_user(session, email=f"growth@{DOMAIN}", plan="growth")
        _ensure_user(session, email=f"pro@{DOMAIN}", plan="pro")

    print("Demo accounts ready (password for all):", DEMO_PASSWORD)
    print()
    print("Team workspace @", DOMAIN)
    print(f"  {owner_email:36}  agency   owner   (unlimited campaigns)")
    print(f"  {admin_email:36}  agency   admin")
    print(f"  {member_email:36}  growth   member  (30 campaigns/mo, … growth limits)")
    print()
    print("Individual tier demos @", DOMAIN)
    print(f"  {'free@' + DOMAIN:36}  free     (2 campaigns/mo, 5 posts/campaign, 1 platform)")
    print(f"  {'starter@' + DOMAIN:36}  starter  (10 / 10 / 2)")
    print(f"  {'growth@' + DOMAIN:36}  growth   (30 / 20 / 3)")
    print(f"  {'pro@' + DOMAIN:36}  pro      (unlimited campaigns & posts, all platforms)")
    print()
    print(
        "To remove monthly campaign limits for any other email (e.g. legacy test admin), add to .env:\n"
        "  BROKERAI_UNLIMITED_CAMPAIGNS_EMAILS=admin@yourcompany.com\n"
    )


if __name__ == "__main__":
    main()
