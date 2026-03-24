"""Lightweight analytics from SQLModel data — no external analytics stack."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session, select

from backend.integrations.ayrshare import (
    extract_ayrshare_post_id,
    fetch_ayrshare_post_analytics,
    normalize_platforms,
)
from backend.models import Campaign, Post


def get_user_stats(session: Session, user_id: int) -> Dict[str, Any]:
    """Post-level metrics for a user."""
    stmt = select(Post).where(Post.user_id == user_id)
    rows = list(session.exec(stmt).all())
    total_posts = len(rows)
    posts_published = sum(1 for r in rows if r.status == "published")
    posts_failed = sum(1 for r in rows if r.status == "publish_failed")
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


def _pick_int(d: Dict[str, Any], *keys: str) -> int:
    for k in keys:
        if k not in d or d[k] is None:
            continue
        try:
            return int(float(d[k]))
        except (TypeError, ValueError):
            continue
    return 0


def _fb_reaction_total(a: Dict[str, Any]) -> int:
    r = a.get("reactions")
    if isinstance(r, dict) and r.get("total") is not None:
        return _pick_int(r, "total")
    return 0


def _aggregate_ayrshare_analytics_body(body: Any) -> Tuple[int, int, int, int, float]:
    """Sum metrics across platform blocks in Ayrshare POST /analytics/post JSON."""
    if not isinstance(body, dict):
        return 0, 0, 0, 0, 0.0
    likes = comments = shares = impressions = 0
    for _, block in body.items():
        if not isinstance(block, dict):
            continue
        a = block.get("analytics")
        if not isinstance(a, dict):
            continue
        comments += _pick_int(a, "commentsCount", "commentCount", "comments")
        shares += _pick_int(a, "sharesCount", "shareCount", "shares")
        lk = _pick_int(a, "likeCount", "likes", "numLikes", "favoriteCount")
        if lk == 0:
            lk = _fb_reaction_total(a)
        likes += lk
        imp = _pick_int(
            a,
            "impressionsUnique",
            "postImpressionsUnique",
            "totalVideoImpressions",
            "totalVideoImpressionsUnique",
            "impressions",
            "reach",
            "videoViews",
            "videoPlayCount",
            "mediaView",
        )
        if imp == 0:
            imp = _pick_int(a, "engagementCount", "engagements")
        impressions += max(imp, 0)

    engaged = likes + comments + shares
    if impressions > 0:
        rate = min(100.0, round(engaged / impressions * 100, 2))
    else:
        rate = min(100.0, float(min(engaged * 2, 100)))
    return likes, comments, shares, max(impressions, engaged), rate


def _placeholder_metrics(post_id: int) -> Tuple[int, int, int, int, float]:
    """Deterministic demo metrics when Ayrshare id or API is unavailable."""
    r = (post_id * 1103515245 + 12345) & 0x7FFFFFFF
    likes = 5 + (r % 120)
    comments = 1 + (r // 7 % 30)
    shares = r // 13 % 25
    impressions = max(80 + (r % 4000), likes + comments + shares + 1)
    engaged = likes + comments + shares
    rate = round(min(100.0, engaged / impressions * 100), 2)
    return likes, comments, shares, impressions, rate


async def fetch_post_analytics(session: Session, post_id: int, user_id: int) -> Dict[str, Any]:
    """
    Load or refresh performance metrics for one post.
    Uses Ayrshare POST /api/analytics/post when publish response contains an Ayrshare id;
    otherwise stores deterministic placeholder metrics.
    Persists likes, comments, shares, impressions, engagement_rate on the Post row.
    """
    row = session.get(Post, post_id)
    if row is None or row.user_id != user_id:
        return {"error": "not_found", "post_id": post_id}

    plat_list: List[str] = []
    if isinstance(row.publish_platforms, list):
        plat_list = normalize_platforms([str(x) for x in row.publish_platforms])

    pr = row.platform_response or "{}"
    try:
        stored = json.loads(pr) if isinstance(pr, str) else pr
    except json.JSONDecodeError:
        stored = {}
    ayr_id = extract_ayrshare_post_id(stored)

    source = "placeholder"
    if ayr_id:
        result = await fetch_ayrshare_post_analytics(ayr_id, plat_list or None)
        if result.get("ok") and isinstance(result.get("body"), dict):
            likes, comments, shares, impressions, engagement_rate = _aggregate_ayrshare_analytics_body(
                result["body"]
            )
            source = "ayrshare"
        else:
            likes, comments, shares, impressions, engagement_rate = _placeholder_metrics(post_id)
            source = "placeholder"
    else:
        likes, comments, shares, impressions, engagement_rate = _placeholder_metrics(post_id)

    row.likes = likes
    row.comments = comments
    row.shares = shares
    row.impressions = impressions
    row.engagement_rate = engagement_rate
    session.add(row)
    session.commit()
    session.refresh(row)

    return {
        "post_id": post_id,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "impressions": impressions,
        "engagement_rate": engagement_rate,
        "source": source,
    }
