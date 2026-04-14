"""Seed a demo account with pre-filled campaigns + posts.

Usage:
    python -m scripts.seed_demo [--reset]

Creates/updates `demo@brokerai.app` (password: `demo1234`) with:
- 2 campaigns ("Austin Open House", "First-Time Buyer Funnel")
- 14 review-ready posts across Facebook / Instagram / LinkedIn
- Fake social_connected=True so the dashboard renders fully

`--reset` drops the demo user's campaigns and posts before seeding (ids preserved
only for the user itself so the login credential stays stable across reseeds).

Safe to run on any env: idempotent, only touches rows for the demo account.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

# Let users run this without setting PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select  # noqa: E402

from backend.auth import hash_password  # noqa: E402
from backend.db import create_db_and_tables, engine  # noqa: E402
from backend.models import Campaign, Post, User  # noqa: E402


DEMO_EMAIL = "demo@brokerai.app"
DEMO_PASSWORD = "demo1234"

CAMPAIGNS: list[dict] = [
    {
        "name": "Austin Open House — Spring 2026",
        "objective": "Drive foot traffic to 1201 Oak St open house on Saturday 2–4pm.",
        "target_audience": "First-time buyers in Austin, TX, 28–42, household income $90k+",
        "status": "pending_approval",
        "posts": [
            ("Facebook", "Open house this Saturday 2–4pm at 1201 Oak St. Modern 3BR in a walkable Austin neighborhood — come grab a coffee, tour the home, and ask anything.",
             ["#AustinHomes", "#OpenHouse", "#RealEstateAustin"]),
            ("Instagram", "Light-filled kitchen, fenced backyard, and a primary suite that actually fits a king bed. 1201 Oak St — open this Saturday.",
             ["#AustinRealEstate", "#DreamHome", "#HouseHunting"]),
            ("LinkedIn", "Helping a buyer find a first home in Austin this spring? 1201 Oak St hits the sweet spot — move-in ready, priced fairly for the block, and within 15 min of downtown. Open Sat 2–4pm.",
             ["#RealEstate", "#AustinTX", "#FirstTimeBuyer"]),
            ("Facebook", "Five things buyers love about 1201 Oak St: updated kitchen, new roof (2024), quiet street, great schools, and a backyard built for summer. DM for a private tour.",
             ["#Austin", "#HomeBuying", "#OpenHouse"]),
            ("Instagram", "Swipe through — morning light in the living room hits different. 1201 Oak St open Saturday.",
             ["#InteriorInspo", "#AustinLiving", "#RealtorLife"]),
            ("LinkedIn", "Market note for Austin: inventory up 12% YoY, but move-in-ready homes under $700k still move in under 10 days. If you're waiting for a 'better' market, this might be it.",
             ["#MarketUpdate", "#Austin", "#HousingMarket"]),
            ("Facebook", "Real talk: the right house doesn't wait. If 1201 Oak St feels like yours, Saturday is the day. See you 2–4pm.",
             ["#OpenHouse", "#AustinHomes", "#RealEstate"]),
        ],
    },
    {
        "name": "First-Time Buyer Funnel",
        "objective": "Educate + capture first-time buyer leads over 7 days.",
        "target_audience": "Renters in Texas metro areas, 26–38, considering buying within 12 months",
        "status": "draft",
        "posts": [
            ("Instagram", "You don't need 20% down. Here's what actually works for first-time buyers in 2026 (swipe).",
             ["#FirstTimeHomeBuyer", "#RealEstateTips", "#DownPayment"]),
            ("Facebook", "Renting vs. owning in Austin this year — the 5-year math, with real numbers. Save this post.",
             ["#RentVsBuy", "#FirstTimeBuyer", "#Austin"]),
            ("LinkedIn", "A simple 4-step plan to go from renter to homeowner in 12 months. Step 1 isn't 'save more' — it's run the numbers honestly.",
             ["#HomeBuying", "#FinancialLiteracy", "#RealEstate"]),
            ("Instagram", "Closing costs: what's real, what's negotiable, what to fight for. 60-sec breakdown.",
             ["#ClosingCosts", "#HomeBuying", "#RealEstateTips"]),
            ("Facebook", "Pre-approval in 15 minutes — here's the paperwork to have ready before you talk to a lender.",
             ["#PreApproval", "#Mortgage", "#FirstTimeBuyer"]),
            ("LinkedIn", "The 3 biggest mistakes I see first-time buyers make — and how to avoid every one of them.",
             ["#RealEstate", "#HomeBuyingTips", "#Mortgage"]),
            ("Instagram", "DM 'guide' and I'll send you the First-Time Buyer Playbook — free, no strings.",
             ["#HomeBuying", "#FreeResource", "#LeadMagnet"]),
        ],
    },
]


def _wipe_demo(session: Session, user_id: int) -> None:
    for post in session.exec(select(Post).where(Post.user_id == user_id)).all():
        session.delete(post)
    for camp in session.exec(select(Campaign).where(Campaign.user_id == user_id)).all():
        session.delete(camp)
    session.commit()


def _ensure_user(session: Session) -> User:
    existing = session.exec(select(User).where(User.email == DEMO_EMAIL)).first()
    if existing is not None:
        existing.password_hash = hash_password(DEMO_PASSWORD)
        existing.social_connected = True
        existing.ayrshare_profile_key = existing.ayrshare_profile_key or "demo-profile-key"
        existing.timezone = existing.timezone or "America/Chicago"
        existing.plan = existing.plan or "pro"
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    user = User(
        email=DEMO_EMAIL,
        password_hash=hash_password(DEMO_PASSWORD),
        timezone="America/Chicago",
        plan="pro",
        ayrshare_profile_key="demo-profile-key",
        social_connected=True,
        brand_voice="warm, confident, concise",
        brand_primary_color="#f59e0b",
        brand_secondary_color="#0f172a",
        brand_font="Inter",
        account_type="individual",
        role="owner",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _seed_campaigns(session: Session, user: User) -> tuple[int, int]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    camp_count = 0
    post_count = 0
    for i, camp_spec in enumerate(CAMPAIGNS):
        camp = Campaign(
            user_id=user.id,
            name=camp_spec["name"],
            objective=camp_spec["objective"],
            target_audience=camp_spec["target_audience"],
            status=camp_spec["status"],
            created_by=user.id,
            created_at=now - timedelta(days=3 - i),
            updated_at=now,
        )
        session.add(camp)
        session.commit()
        session.refresh(camp)
        camp_count += 1

        for day, (platform, caption, tags) in enumerate(camp_spec["posts"]):
            session.add(
                Post(
                    user_id=user.id,
                    campaign_id=camp.id,
                    caption=caption,
                    content=caption,
                    platform=platform.lower(),
                    publish_platforms=[platform.lower()],
                    hashtags=json.dumps(tags),
                    image_url=f"https://picsum.photos/seed/brokerai-demo-{camp.id}-{day}/1024/1024",
                    day_label=f"Day {day + 1}",
                    status="review",
                    scheduled_at=now + timedelta(days=day, hours=9),
                )
            )
            post_count += 1
        session.commit()
    return camp_count, post_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the BrokerAI demo account.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing demo campaigns/posts before seeding.",
    )
    args = parser.parse_args()

    create_db_and_tables()
    with Session(engine) as session:
        user = _ensure_user(session)
        if args.reset:
            _wipe_demo(session, user.id)
        existing = session.exec(
            select(Campaign).where(Campaign.user_id == user.id)
        ).first()
        if existing and not args.reset:
            print(
                f"Demo already seeded for {DEMO_EMAIL}. "
                "Rerun with --reset to refresh."
            )
            return
        camps, posts = _seed_campaigns(session, user)
    print(
        f"Seeded {camps} campaigns and {posts} posts for {DEMO_EMAIL} "
        f"(password: {DEMO_PASSWORD})."
    )


if __name__ == "__main__":
    main()
