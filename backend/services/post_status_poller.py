"""
S1-02 — Background analytics refresh + stuck-post recovery.

Runs every 60 s (same cadence as the publish scheduler).
Two passes per tick:
  1.  recover_stuck_jobs()     — clears expired publish locks (already in publish_service)
  2.  analytics_refresh_pass() — re-fetches Ayrshare metrics for recently-published posts
                                  that still show zero engagement OR haven't been refreshed
                                  in the last REFRESH_AFTER_HOURS hours.

At most MAX_POSTS_PER_TICK posts are refreshed per tick so we never flood Ayrshare.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import List

from sqlmodel import Session, select

from backend.core.logger import get_logger
from backend.db import engine
from backend.models import Post
from backend.services.analytics_service import fetch_post_analytics
from backend.services.publish_service import recover_stuck_jobs

log = get_logger("brokerai.poller")

# Posts published within this window are eligible for analytics refresh
REFRESH_WINDOW_DAYS: int = 7
# Minimum gap before refreshing a post that already has non-zero analytics
REFRESH_AFTER_HOURS: int = 2
# Maximum posts refreshed per 60-second tick (rate-limit buffer)
MAX_POSTS_PER_TICK: int = 10


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _eligible_post_ids(session: Session) -> List[int]:
    """
    Return post IDs eligible for analytics refresh this tick.

    Selection criteria:
      - status = 'published'
      - published_at within the last REFRESH_WINDOW_DAYS days
      - social_post_id is non-empty  (we need an Ayrshare id to query)
      - EITHER likes + comments == 0  (never successfully pulled real metrics)
        OR published more recently than REFRESH_AFTER_HOURS ago
    """
    now = _utc_now()
    cutoff = now - timedelta(days=REFRESH_WINDOW_DAYS)
    recent_cutoff = now - timedelta(hours=REFRESH_AFTER_HOURS)

    stmt = (
        select(Post)
        .where(Post.status == "published")
        .where(Post.published_at.is_not(None))
        .where(Post.published_at >= cutoff)
        .where(Post.social_post_id != "")
        .where(Post.social_post_id.is_not(None))
    )
    rows = list(session.exec(stmt).all())

    eligible: List[int] = []
    for row in rows:
        if row.id is None:
            continue
        has_real_metrics = (int(row.likes or 0) + int(row.comments or 0)) > 0
        published_recently = (
            row.published_at is not None and row.published_at >= recent_cutoff
        )
        # Refresh if: no metrics yet, or published very recently (metrics still growing)
        if not has_real_metrics or published_recently:
            eligible.append(int(row.id))

    # Oldest first so we don't keep skipping the same recent posts
    eligible.sort()
    return eligible[:MAX_POSTS_PER_TICK]


async def analytics_refresh_pass() -> int:
    """Refresh analytics for eligible posts. Returns number of posts attempted."""
    with Session(engine) as session:
        post_ids = _eligible_post_ids(session)

    if not post_ids:
        return 0

    log.debug("poller_analytics_refresh candidates=%s", len(post_ids))
    attempted = 0
    for pid in post_ids:
        try:
            with Session(engine) as session:
                row = session.get(Post, pid)
                if row is None or row.user_id is None:
                    continue
                uid = int(row.user_id)
            result = await fetch_post_analytics(
                # fetch_post_analytics opens its own session internally; pass fresh one
                Session(engine).__enter__(),
                pid,
                uid,
            )
            attempted += 1
            if result.get("source") == "ayrshare":
                log.debug(
                    "poller_analytics_updated post_id=%s likes=%s impressions=%s",
                    pid,
                    result.get("likes"),
                    result.get("impressions"),
                )
        except Exception:
            log.warning("poller_analytics_failed post_id=%s", pid, exc_info=True)

        # Small stagger between requests to avoid bursting the Ayrshare API
        await asyncio.sleep(0.5)

    return attempted


async def status_poll_tick() -> None:
    """One full tick: recover stuck jobs then refresh analytics."""
    # Pass 1: recover any publish jobs with expired locks
    try:
        with Session(engine) as session:
            recovered = recover_stuck_jobs(session)
        if recovered:
            log.info("poller_recovered_stuck_jobs count=%s", recovered)
    except Exception:
        log.warning("poller_recover_stuck_jobs failed", exc_info=True)

    # Pass 2: refresh analytics for recently-published posts
    if not os.getenv("BROKERAI_DISABLE_POLLER"):
        try:
            n = await analytics_refresh_pass()
            if n:
                log.debug("poller_analytics_pass refreshed=%s", n)
        except Exception:
            log.warning("poller_analytics_refresh_pass failed", exc_info=True)
