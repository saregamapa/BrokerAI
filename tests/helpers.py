"""Shared test helpers (no pytest imports)."""
from __future__ import annotations

import os
from typing import Optional

from sqlmodel import Session, select

from backend.auth import hash_password
from backend.db import engine
from backend.models import Campaign, Post, User


def create_test_user(
    email: str,
    *,
    password: str = "secret12",
    account_type: str = "individual",
    role: str = "owner",
    team_id: Optional[int] = None,
) -> int:
    """Insert a user row directly (no HTTP signup). Returns user id."""
    em = email.strip().lower()
    with Session(engine) as s:
        hit = s.exec(select(User).where(User.email == em)).first()
        if hit:
            return int(hit.id)
        u = User(
            email=em,
            password_hash=hash_password(password),
            account_type=account_type,
            role=role,
            team_id=team_id,
        )
        s.add(u)
        s.commit()
        s.refresh(u)
        return int(u.id)


def user_headers(user_id: int) -> dict:
    """Select which DB user the API acts as (pytest only)."""
    if os.getenv("PYTEST_CURRENT_TEST"):
        return {"X-BrokerAI-User-Id": str(int(user_id))}
    return {}


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
