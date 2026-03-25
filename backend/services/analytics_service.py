"""
Post performance ingestion: Ayrshare analytics API with realistic simulation fallback.

Engagement rate (product): (likes + comments) / max(impressions, 1) × 100 (shares still stored separately).
"""
from __future__ import annotations

import json
import random
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session, select

from backend.core.logger import get_logger
from backend.integrations.ayrshare import (
    extract_ayrshare_post_id,
    fetch_ayrshare_post_analytics,
    normalize_platforms,
)
from backend.models import Post, User

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


def simulated_metrics(seed: int) -> Tuple[int, int, int, int, float]:
    """Randomized realistic metrics when Ayrshare is unavailable (non-blocking)."""
    rng = random.Random(seed)
    likes = rng.randint(10, 500)
    comments = rng.randint(1, 50)
    impressions = rng.randint(500, 10000)
    impressions = max(impressions, likes + comments + 1)
    shares = rng.randint(0, min(80, likes // 3 + 5))
    rate = compute_engagement_rate(likes, comments, impressions)
    return likes, comments, shares, impressions, rate


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
    Pull metrics from Ayrshare when possible; otherwise simulated data.
    Persists likes, comments, shares, impressions, engagement_rate.
    """
    row = session.get(Post, post_id)
    if row is None or row.user_id != user_id:
        return {"error": "not_found", "post_id": post_id}

    owner = session.get(User, user_id)
    profile_key = (owner.ayrshare_profile_key or "").strip() if owner is not None else ""

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
            likes, comments, shares, impressions, engagement_rate = simulated_metrics(
                post_id * 9973 + user_id
            )
            source = "placeholder"
    else:
        likes, comments, shares, impressions, engagement_rate = simulated_metrics(
            post_id * 9973 + user_id
        )
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
