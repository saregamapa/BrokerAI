"""Shared test helpers (no pytest imports)."""
from sqlmodel import Session

from backend.db import engine
from backend.models import Campaign, Post, User


def mark_user_social_connected(
    user_id: int, profile_key: str = "pytest-ayrshare-profile-key"
) -> None:
    """Tests bypass real Ayrshare SSO; publishing is mocked where needed."""
    with Session(engine) as s:
        u = s.get(User, user_id)
        assert u is not None
        u.ayrshare_profile_key = profile_key
        u.social_connected = True
        s.add(u)
        s.commit()


def stub_run_campaign_phase1(initial: dict, thread_id: str) -> None:
    """Minimal LangGraph phase-1 stand-in: mark campaign ready and add sample posts."""
    uid = initial["user_id"]
    cid = initial["campaign_id"]
    with Session(engine) as s:
        camp = s.get(Campaign, cid)
        assert camp is not None
        camp.status = "pending_approval"
        s.add(camp)
        for i in range(3):
            s.add(
                Post(
                    user_id=uid,
                    campaign_id=cid,
                    caption=f"Test caption {i}",
                    hashtags="[]",
                    status="pending_approval",
                )
            )
        s.commit()
