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
    """Test stand-in for LangGraph phase-1 (real pipeline requires OpenAI)."""
    uid = initial["user_id"]
    cid = initial["campaign_id"]
    with Session(engine) as s:
        camp = s.get(Campaign, cid)
        assert camp is not None
        camp.status = "pending_approval"
        s.add(camp)
        for i in range(7):
            s.add(
                Post(
                    user_id=uid,
                    campaign_id=cid,
                    caption=f"Test caption day {i + 1} for pytest.",
                    hashtags='["#realestate","#test"]',
                    image_url=f"https://picsum.photos/seed/brokerai{i}/1024/1024",
                    video_script="{}",
                    status="review",
                )
            )
        s.commit()
