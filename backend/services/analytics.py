"""Lightweight analytics from SQLModel data — no external analytics stack."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from backend.models import Campaign, Post

# Re-export for scheduler, /post-analytics, and bulk refresh
from backend.services.analytics_service import (  # noqa: F401
    fetch_post_analytics,
    update_post_analytics,
)


def get_user_stats(session: Session, user_id: int) -> Dict[str, Any]:
    """Post-level metrics for a user."""
    stmt = select(Post).where(Post.user_id == user_id)
    rows = list(session.exec(stmt).all())
    total_posts = len(rows)
    posts_published = sum(1 for r in rows if r.status == "published")
    posts_failed = sum(1 for r in rows if r.status == "failed")
    attempted = posts_published + posts_failed
    if attempted > 0:
        success_rate = int(round((posts_published / attempted) * 100))
    else:
        success_rate = 0

    last_published_at: Optional[datetime] = None
    for r in rows:
        if r.published_at and (last_published_at is None or r.published_at > last_published_at):
            last_published_at = r.published_at

    last_iso = None
    if last_published_at:
        last_iso = last_published_at.isoformat()
        if last_published_at.tzinfo is None:
            last_iso += "Z"

    return {
        "total_posts": total_posts,
        "posts_published": posts_published,
        "posts_failed": posts_failed,
        "success_rate": success_rate,
        "last_published_at": last_iso,
    }


def get_campaign_stats(session: Session, user_id: int) -> Dict[str, Any]:
    """Campaign counts and optional per-campaign rollups."""
    stmt = select(Campaign).where(Campaign.user_id == user_id)
    camps = list(session.exec(stmt).all())
    total_campaigns = len(camps)

    by_status: Dict[str, int] = {}
    for c in camps:
        by_status[c.status] = by_status.get(c.status, 0) + 1

    return {
        "total_campaigns": total_campaigns,
        "campaigns_by_status": by_status,
    }


def get_analytics_payload(session: Session, user_id: int) -> Dict[str, Any]:
    """Merged response for GET /analytics."""
    u = get_user_stats(session, user_id)
    c = get_campaign_stats(session, user_id)
    return {
        "total_campaigns": c["total_campaigns"],
        "total_posts": u["total_posts"],
        "posts_published": u["posts_published"],
        "posts_failed": u["posts_failed"],
        "success_rate": u["success_rate"],
        "last_published_at": u["last_published_at"],
    }
