"""
S5-09 — Soft-delete helpers.
Campaigns and Posts are never physically deleted — deleted_at is set instead.
Recovery is possible within 30 days.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from sqlmodel import Session, select

from backend.core.logger import get_logger
from backend.models import Campaign, Post

log = get_logger("brokerai.soft_delete")

RECOVERY_WINDOW_DAYS = 30


def soft_delete_campaign(session: Session, campaign_id: int, user_id: int) -> bool:
    """Soft-delete a campaign. Returns True if deleted, False if not found/not owned."""
    campaign = session.get(Campaign, campaign_id)
    if campaign is None or campaign.user_id != user_id or campaign.deleted_at is not None:
        return False
    campaign.deleted_at = datetime.utcnow()
    session.add(campaign)
    # Also soft-delete all posts in the campaign
    posts = list(session.exec(
        select(Post).where(Post.campaign_id == campaign_id).where(Post.deleted_at == None)
    ).all())
    for post in posts:
        post.deleted_at = datetime.utcnow()
        session.add(post)
    session.commit()
    log.info("soft_delete_campaign campaign_id=%s user_id=%s posts_deleted=%d", campaign_id, user_id, len(posts))
    return True


def restore_campaign(session: Session, campaign_id: int, user_id: int) -> bool:
    """Restore a soft-deleted campaign within the recovery window."""
    campaign = session.get(Campaign, campaign_id)
    if campaign is None or campaign.user_id != user_id or campaign.deleted_at is None:
        return False
    if datetime.utcnow() - campaign.deleted_at > timedelta(days=RECOVERY_WINDOW_DAYS):
        return False  # Recovery window expired
    campaign.deleted_at = None
    session.add(campaign)
    # Also restore posts
    posts = list(session.exec(
        select(Post).where(Post.campaign_id == campaign_id).where(Post.deleted_at != None)
    ).all())
    for post in posts:
        post.deleted_at = None
        session.add(post)
    session.commit()
    log.info("restore_campaign campaign_id=%s user_id=%s posts_restored=%d", campaign_id, user_id, len(posts))
    return True


def soft_delete_post(session: Session, post_id: int, user_id: int) -> bool:
    """Soft-delete a single post."""
    post = session.get(Post, post_id)
    if post is None or post.deleted_at is not None:
        return False
    # Verify ownership via campaign
    campaign = session.get(Campaign, post.campaign_id) if post.campaign_id else None
    if campaign is None or campaign.user_id != user_id:
        return False
    post.deleted_at = datetime.utcnow()
    session.add(post)
    session.commit()
    return True


def purge_expired_soft_deletes(session: Session) -> int:
    """Permanently delete records past the 30-day recovery window. Called by scheduler."""
    cutoff = datetime.utcnow() - timedelta(days=RECOVERY_WINDOW_DAYS)
    expired_campaigns = list(session.exec(
        select(Campaign).where(Campaign.deleted_at != None).where(Campaign.deleted_at < cutoff)
    ).all())
    count = 0
    for c in expired_campaigns:
        # Physically delete posts first
        posts = list(session.exec(select(Post).where(Post.campaign_id == c.id)).all())
        for p in posts:
            session.delete(p)
        session.delete(c)
        count += 1
    if count:
        session.commit()
        log.info("purge_expired_soft_deletes campaigns=%d cutoff=%s", count, cutoff.date())
    return count
