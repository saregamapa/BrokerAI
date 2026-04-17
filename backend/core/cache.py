"""
Optional Redis cache layer.
Falls back to no-op when REDIS_URL is not configured.
All cache operations are fire-and-forget — never raise exceptions to callers.
"""
import os
import json
import logging
from typing import Any, Optional

log = logging.getLogger("brokerai.cache")

_redis_client = None
_redis_available: Optional[bool] = None  # None = not yet probed


def _get_client():
    """Lazy-init Redis client. Returns None if not configured or unavailable."""
    global _redis_client, _redis_available
    if _redis_available is not None:
        return _redis_client if _redis_available else None

    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        log.debug("REDIS_URL not set — cache disabled")
        _redis_available = False
        return None

    try:
        import redis  # type: ignore

        client = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        _redis_client = client
        _redis_available = True
        log.info("redis_connected url_prefix=%s", redis_url[:20])
        return client
    except Exception as e:
        log.warning("redis_connect_failed err=%s — caching disabled", e)
        _redis_available = False
        return None


def cache_get(key: str) -> Optional[Any]:
    """Get a cached JSON value. Returns None on miss or error."""
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
        return json.loads(raw) if raw else None
    except Exception as e:
        log.debug("cache_get_failed key=%s err=%s", key, e)
        return None


def cache_set(key: str, value: Any, ttl_seconds: int = 300) -> bool:
    """Set a JSON value with TTL. Returns True on success."""
    client = _get_client()
    if client is None:
        return False
    try:
        client.setex(key, ttl_seconds, json.dumps(value, default=str))
        return True
    except Exception as e:
        log.debug("cache_set_failed key=%s err=%s", key, e)
        return False


def cache_delete(key: str) -> bool:
    """Delete a cache key."""
    client = _get_client()
    if client is None:
        return False
    try:
        client.delete(key)
        return True
    except Exception as e:
        log.debug("cache_delete_failed key=%s err=%s", key, e)
        return False


def cache_delete_pattern(pattern: str) -> int:
    """Delete all keys matching a pattern. Returns count deleted."""
    client = _get_client()
    if client is None:
        return 0
    try:
        keys = list(client.scan_iter(pattern))
        if keys:
            return client.delete(*keys)
        return 0
    except Exception as e:
        log.debug("cache_delete_pattern_failed pattern=%s err=%s", pattern, e)
        return 0


# ── Standard TTLs ────────────────────────────────────────────────────────────

CAMPAIGN_LIST_TTL = 300       # 5 minutes
ANALYTICS_SUMMARY_TTL = 300   # 5 minutes


# ── Key helpers ───────────────────────────────────────────────────────────────

def campaign_list_key(user_id: int) -> str:
    return f"brokerai:campaigns:user:{user_id}"


def analytics_summary_key(user_id: int) -> str:
    return f"brokerai:analytics:user:{user_id}"


def campaign_detail_key(campaign_id: int) -> str:
    return f"brokerai:campaign:{campaign_id}"


# ── Invalidation helpers ──────────────────────────────────────────────────────

def invalidate_user_campaigns(user_id: int) -> None:
    """Call whenever campaigns are created, updated, or deleted for a user."""
    cache_delete(campaign_list_key(user_id))


def invalidate_user_analytics(user_id: int) -> None:
    """Call after analytics data is refreshed for a user."""
    cache_delete(analytics_summary_key(user_id))
