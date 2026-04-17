"""
Post performance ingestion: Ayrshare analytics API when a post id exists; otherwise zeros.

Engagement rate (product): (likes + comments) / max(impressions, 1) × 100 (shares still stored separately).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session, select

from backend.core.logger import get_logger
from backend.integrations.ayrshare import (
    extract_ayrshare_post_id,
    fetch_ayrshare_post_analytics,
    normalize_platforms,
)
from backend.models import Post, User
from backend.services.team_service import resolve_ayrshare_subject_user

log = get_logger("brokerai.analytics_service")


def compute_engagement_rate(likes: int, comments: int, impressions: int) -> float:
    if impressions <= 0:
        return 0.0
    return round(min(100.0, (likes + comments) / impressions * 100), 2)


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


def aggregate_ayrshare_analytics_body(body: Any) -> Tuple[int, int, int, int]:
    """Sum likes, comments, shares, impressions from Ayrshare POST /analytics/post JSON."""
    if not isinstance(body, dict):
        return 0, 0, 0, 0
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

    engaged_floor = likes + comments + 1
    impressions = max(impressions, engaged_floor)
    return likes, comments, shares, impressions


def _resolve_ayrshare_id(row: Post, stored: Any) -> str:
    sid = (getattr(row, "social_post_id", None) or "").strip()
    if sid:
        return sid
    return (extract_ayrshare_post_id(stored) or "").strip()


def _platform_list_for_row(row: Post, override: Optional[str]) -> List[str]:
    if override and str(override).strip():
        return normalize_platforms([str(override).strip()])
    plats: List[str] = []
    if isinstance(row.publish_platforms, list):
        plats = normalize_platforms([str(x) for x in row.publish_platforms])
    row_plat = (getattr(row, "platform", None) or "").strip()
    if row_plat:
        plats = normalize_platforms([row_plat] + plats)
    return plats or ["facebook"]


async def fetch_post_analytics(
    session: Session,
    post_id: int,
    user_id: int,
    *,
    platform: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Pull metrics from Ayrshare when possible; otherwise persist zeros (no fabricated metrics).
    """
    row = session.get(Post, post_id)
    if row is None or row.user_id != user_id:
        return {"error": "not_found", "post_id": post_id}

    post_owner = session.get(User, user_id)
    subject = (
        resolve_ayrshare_subject_user(session, post_owner)
        if post_owner is not None
        else None
    )
    profile_key = (
        (subject.ayrshare_profile_key or "").strip() if subject is not None else ""
    )

    plat_list = _platform_list_for_row(row, platform)

    pr = row.platform_response or "{}"
    try:
        stored = json.loads(pr) if isinstance(pr, str) else pr
    except json.JSONDecodeError:
        stored = {}

    ayr_id = _resolve_ayrshare_id(row, stored)

    source = "placeholder"
    likes = comments = shares = impressions = 0
    engagement_rate = 0.0

    if ayr_id:
        try:
            result = await fetch_ayrshare_post_analytics(
                ayr_id, plat_list or None, profile_key=profile_key or None
            )
        except Exception:
            log.warning("ayrshare analytics request failed post_id=%s", post_id, exc_info=True)
            result = {"ok": False, "body": {}}

        if result.get("ok") and isinstance(result.get("body"), dict):
            likes, comments, shares, impressions = aggregate_ayrshare_analytics_body(
                result["body"]
            )
            engagement_rate = compute_engagement_rate(likes, comments, impressions)
            source = "ayrshare"
        else:
            source = "placeholder"
    else:
        source = "placeholder"

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


async def update_post_analytics(session: Session, user_id: int) -> Dict[str, Any]:
    """Refresh analytics for all of the user's posts (errors per row are logged, not raised)."""
    stmt = select(Post).where(Post.user_id == user_id)
    rows = list(session.exec(stmt).all())
    ok = errors = 0
    for row in rows:
        if row.id is None:
            continue
        try:
            data = await fetch_post_analytics(session, int(row.id), user_id)
            if data.get("error"):
                errors += 1
            else:
                ok += 1
        except Exception:
            log.exception("update_post_analytics failed post_id=%s", row.id)
            errors += 1
    return {"updated": ok, "failed": errors, "total": len(rows)}


def list_user_posts_for_analytics(session: Session, user_id: int) -> List[Post]:
    stmt = (
        select(Post)
        .where(Post.user_id == user_id)
        .order_by(Post.engagement_rate.desc(), Post.id.desc())
    )
    return list(session.exec(stmt).all())


def performance_tier(
    engagement_rate: float,
    status: str,
    published_rates: List[float],
) -> str:
    if status != "published":
        return "pending"
    if not published_rates:
        return "mid"
    sr = sorted(published_rates)
    n = len(sr)
    hi = sr[int(0.75 * (n - 1))] if n else 0.0
    lo = sr[int(0.25 * (n - 1))] if n else 0.0
    if engagement_rate >= hi and engagement_rate > 0:
        return "top"
    if engagement_rate <= lo and n > 2:
        return "low"
    return "mid"


def build_analytics_summary(session: Session, user_id: int) -> Dict[str, Any]:
    rows = list(session.exec(select(Post).where(Post.user_id == user_id)).all())
    published = [r for r in rows if r.status == "published"]
    rates = [float(r.engagement_rate or 0) for r in published]
    avg_rate = round(sum(rates) / len(rates), 2) if rates else 0.0
    total_impressions = sum(int(r.impressions or 0) for r in rows)
    total_likes = sum(int(r.likes or 0) for r in rows)
    total_comments = sum(int(r.comments or 0) for r in rows)

    def sort_key(p: Post) -> float:
        return float(p.engagement_rate or 0)

    best = max(published, key=sort_key, default=None)
    worst = min(published, key=sort_key, default=None) if len(published) > 1 else None
    if worst is not None and worst.id == best.id:
        worst = None

    return {
        "avg_engagement_rate": avg_rate,
        "total_impressions": total_impressions,
        "total_likes": total_likes,
        "total_comments": total_comments,
        "best_post_id": best.id if best else None,
        "worst_post_id": worst.id if worst else None,
        "published_count": len(published),
    }


def platform_breakdown(session: Session, user_id: int) -> List[Dict[str, Any]]:
    """Per-platform aggregated metrics across all posts for a user."""
    rows = list(session.exec(select(Post).where(Post.user_id == user_id)).all())
    buckets: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        plat = (
            (getattr(r, "platform", None) or "").strip()
            or (
                r.publish_platforms[0]
                if isinstance(r.publish_platforms, list) and r.publish_platforms
                else "other"
            )
        ).lower() or "other"
        if plat not in buckets:
            buckets[plat] = {
                "platform": plat,
                "post_count": 0,
                "total_likes": 0,
                "total_comments": 0,
                "total_impressions": 0,
                "total_engagement_rate": 0.0,
                "published_count": 0,
            }
        b = buckets[plat]
        b["post_count"] += 1
        b["total_likes"] += int(r.likes or 0)
        b["total_comments"] += int(r.comments or 0)
        b["total_impressions"] += int(r.impressions or 0)
        if r.status == "published":
            b["total_engagement_rate"] += float(r.engagement_rate or 0)
            b["published_count"] += 1

    result = []
    for plat, b in sorted(buckets.items()):
        pc = b["published_count"]
        avg_er = round(b["total_engagement_rate"] / pc, 2) if pc else 0.0
        result.append(
            {
                "platform": b["platform"],
                "post_count": b["post_count"],
                "published_count": pc,
                "total_likes": b["total_likes"],
                "total_comments": b["total_comments"],
                "total_impressions": b["total_impressions"],
                "avg_engagement_rate": avg_er,
            }
        )
    # Sort by total engagement descending
    result.sort(key=lambda x: x["total_impressions"], reverse=True)
    return result


def time_series(session: Session, user_id: int, days: int = 30) -> List[Dict[str, Any]]:
    """Daily post-performance series for the past `days` days.

    Groups posts by their published_at date (or scheduled_at fallback).
    Returns one dict per calendar day with aggregate metrics.
    """
    from datetime import date, timedelta

    today = date.today()
    cutoff_dt = today - timedelta(days=days - 1)

    # Build a slot per day
    day_index: Dict[str, Dict[str, Any]] = {}
    for i in range(days):
        d = (cutoff_dt + timedelta(days=i)).isoformat()
        day_index[d] = {
            "date": d,
            "post_count": 0,
            "likes": 0,
            "comments": 0,
            "impressions": 0,
            "avg_engagement_rate": 0.0,
            "_er_sum": 0.0,
            "_er_count": 0,
        }

    rows = list(session.exec(select(Post).where(Post.user_id == user_id)).all())
    for r in rows:
        # Use published_at first, then scheduled_at
        ts = r.published_at or r.scheduled_at
        if ts is None:
            continue
        try:
            d = ts.date() if hasattr(ts, "date") else None
            if d is None:
                continue
            key = d.isoformat()
        except (AttributeError, ValueError):
            continue
        if key not in day_index:
            continue
        slot = day_index[key]
        slot["post_count"] += 1
        slot["likes"] += int(r.likes or 0)
        slot["comments"] += int(r.comments or 0)
        slot["impressions"] += int(r.impressions or 0)
        slot["_er_sum"] += float(r.engagement_rate or 0)
        slot["_er_count"] += 1

    out = []
    for key in sorted(day_index.keys()):
        slot = day_index[key]
        cnt = slot["_er_count"]
        slot["avg_engagement_rate"] = round(slot["_er_sum"] / cnt, 2) if cnt else 0.0
        del slot["_er_sum"]
        del slot["_er_count"]
        out.append(slot)
    return out


def posts_as_ai_payload(rows: List[Post]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in rows:
        text = (getattr(r, "content", None) or "").strip() or (r.caption or "")
        out.append(
            {
                "id": r.id,
                "platform": (getattr(r, "platform", None) or "") or (
                    (r.publish_platforms or ["facebook"])[0]
                    if isinstance(r.publish_platforms, list) and r.publish_platforms
                    else "facebook"
                ),
                "content": text[:2000],
                "status": r.status,
                "likes": int(r.likes or 0),
                "comments": int(r.comments or 0),
                "impressions": int(r.impressions or 0),
                "engagement_rate": float(r.engagement_rate or 0),
            }
        )
    return out
