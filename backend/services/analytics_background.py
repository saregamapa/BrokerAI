"""
Async background analytics sync tasks.
Called via FastAPI BackgroundTasks to avoid blocking request threads.
"""
import logging
import asyncio
from datetime import datetime
from typing import Optional

log = logging.getLogger("brokerai.analytics_bg")


async def sync_campaign_analytics(campaign_id: int, user_id: int) -> None:
    """
    Background task: fetch fresh analytics from Ayrshare for a campaign's posts.
    Fire-and-forget — errors are logged but never raised.
    """
    try:
        from backend.db import engine
        from sqlmodel import Session, select
        from backend.models import Post, Campaign

        with Session(engine) as session:
            campaign = session.get(Campaign, campaign_id)
            if not campaign or campaign.user_id != user_id:
                log.warning(
                    "analytics_bg_skip campaign_id=%s not_found_or_not_owner", campaign_id
                )
                return

            posts = list(
                session.exec(
                    select(Post)
                    .where(Post.campaign_id == campaign_id)
                    .where(Post.status == "published")
                ).all()
            )
            # Filter to posts that have a valid social_post_id
            posts = [
                p for p in posts
                if (getattr(p, "social_post_id", None) or "").strip()
            ]

            if not posts:
                log.debug("analytics_bg_no_posts campaign_id=%s", campaign_id)
                return

            log.info(
                "analytics_bg_start campaign_id=%s post_count=%d", campaign_id, len(posts)
            )

        # Fetch analytics for each post via the existing analytics service
        try:
            from backend.services.analytics_service import fetch_post_analytics
        except ImportError:
            log.debug("analytics_bg_no_analytics_service")
            return

        # Also try to import the optional Ayrshare helper for a direct lookup
        try:
            from backend.services.ayrshare_service import get_post_analytics  # type: ignore
            _has_ayrshare_helper = True
        except ImportError:
            _has_ayrshare_helper = False

        refreshed = 0
        for post in posts[:10]:  # cap at 10 per background call to respect rate limits
            try:
                from backend.db import engine
                from sqlmodel import Session

                with Session(engine) as session:
                    data = await fetch_post_analytics(session, int(post.id), user_id)
                    if data and not data.get("error"):
                        refreshed += 1
                await asyncio.sleep(0.3)  # rate limit between Ayrshare calls
            except Exception as e:
                log.debug("analytics_bg_post_fail post_id=%s err=%s", post.id, e)

        log.info("analytics_bg_done campaign_id=%s refreshed=%d", campaign_id, refreshed)

    except Exception:
        log.exception("analytics_bg_unexpected_error campaign_id=%s", campaign_id)


async def sync_user_analytics_summary(user_id: int) -> None:
    """
    Background task: pre-compute user-level analytics summary and cache in memory/DB.
    Triggered after campaign publish or on-demand from dashboard load.
    """
    try:
        log.debug("analytics_summary_bg_start user_id=%s", user_id)
        # Currently a no-op placeholder — extend when caching layer (Redis) is available.
        # This prevents blocking the request while the summary is computed.
        await asyncio.sleep(0)  # yield to event loop
        log.debug("analytics_summary_bg_done user_id=%s", user_id)
    except Exception:
        log.exception("analytics_summary_bg_error user_id=%s", user_id)
