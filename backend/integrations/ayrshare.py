import json
import os
from typing import Any, Dict, List, Optional, Union

import httpx

# Official API host (matches publish docs; app.* may 404 or redirect for some keys)
AYRSHARE_POST_URL = "https://api.ayrshare.com/api/post"
AYRSHARE_ANALYTICS_POST_URL = "https://api.ayrshare.com/api/analytics/post"

# Wizard / API labels → Ayrshare `platforms` slugs (lowercase)
_PLATFORM_ALIASES: Dict[str, str] = {
    "facebook": "facebook",
    "fb": "facebook",
    "instagram": "instagram",
    "ig": "instagram",
    "linkedin": "linkedin",
    "linked_in": "linkedin",
    "twitter": "twitter",
    "x": "twitter",
    "tiktok": "tiktok",
    "youtube": "youtube",
    "pinterest": "pinterest",
}


def normalize_platforms(platforms: List[str]) -> List[str]:
    """Map UI labels to Ayrshare platform ids. Never returns an empty list."""
    out: List[str] = []
    for p in platforms:
        key = str(p).strip().lower().replace(" ", "").replace("_", "")
        slug = _PLATFORM_ALIASES.get(key)
        if slug is None and key in ("linkedin", "facebook", "instagram"):
            slug = key
        if slug and slug not in out:
            out.append(slug)
    return out if out else ["facebook"]


def coerce_ayrshare_platforms(stored: Union[None, str, List[Any]]) -> List[str]:
    """Read platforms from DB (JSON list or legacy JSON string). Never empty."""
    pl: List[str] = []
    if stored is None:
        return normalize_platforms([])
    if isinstance(stored, list):
        pl = [str(x).strip() for x in stored if x is not None and str(x).strip()]
    elif isinstance(stored, str):
        s = stored.strip()
        if s:
            try:
                data = json.loads(s)
                if isinstance(data, list):
                    pl = [str(x).strip() for x in data if str(x).strip()]
            except json.JSONDecodeError:
                pl = []
    return normalize_platforms(pl)


async def publish_post(
    caption: str,
    platforms: List[str],
    media_urls: Optional[List[str]] = None,
    profile_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    POST to Ayrshare. Returns a dict with ok (bool), status_code, and body (parsed or raw).
    User profiles require Profile-Key header (Business Plan).
    """
    api_key = os.getenv("AYRSHARE_API_KEY", "").strip()
    if not api_key:
        return {
            "ok": False,
            "status_code": 0,
            "body": {"error": "missing_env", "detail": "AYRSHARE_API_KEY is not set"},
        }

    pk = (profile_key or "").strip()
    if not pk:
        return {
            "ok": False,
            "status_code": 0,
            "body": {
                "error": "not_connected",
                "detail": "Social accounts not connected",
            },
        }

    normalized = normalize_platforms(platforms)
    if not normalized:
        normalized = ["facebook"]
    payload: Dict[str, Any] = {"post": caption, "platforms": normalized}
    urls = [
        u.strip()
        for u in (media_urls or [])
        if isinstance(u, str)
        and u.strip()
        and u.strip().lower().startswith(("http://", "https://"))
    ]
    if urls:
        payload["mediaUrls"] = urls

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Profile-Key": pk,
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                AYRSHARE_POST_URL,
                json=payload,
                headers=headers,
            )
    except httpx.RequestError as e:
        return {
            "ok": False,
            "status_code": 0,
            "body": {"error": "request_error", "detail": str(e)},
        }

    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}

    ok = 200 <= resp.status_code < 300
    if isinstance(data, dict):
        errs = data.get("errors") or data.get("error")
        posts = data.get("posts")
        if errs and not posts:
            ok = False

    return {"ok": ok, "status_code": resp.status_code, "body": data}


def platform_response_json(result: Dict[str, Any]) -> str:
    return json.dumps(result, default=str)


def extract_ayrshare_post_id(stored: Any) -> Optional[str]:
    """Parse publish result JSON; return Ayrshare post id for /analytics/post."""
    if stored is None:
        return None
    if isinstance(stored, str):
        s = stored.strip()
        if not s:
            return None
        try:
            stored = json.loads(s)
        except json.JSONDecodeError:
            return None
    if not isinstance(stored, dict):
        return None
    body = stored.get("body")
    if not isinstance(body, dict):
        body = stored
    aid = body.get("id")
    if isinstance(aid, str) and aid.strip():
        return aid.strip()
    posts = body.get("posts")
    if isinstance(posts, list) and posts:
        first = posts[0]
        if isinstance(first, dict):
            pid = first.get("id")
            if isinstance(pid, str) and pid.strip():
                return pid.strip()
    return None


async def fetch_ayrshare_post_analytics(
    ayrshare_post_id: str,
    platforms: Optional[List[str]] = None,
    profile_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    POST https://api.ayrshare.com/api/analytics/post
    Returns { ok, status_code, body }.
    """
    api_key = os.getenv("AYRSHARE_API_KEY", "").strip()
    if not api_key:
        return {
            "ok": False,
            "status_code": 0,
            "body": {"error": "missing_env", "detail": "AYRSHARE_API_KEY is not set"},
        }

    payload: Dict[str, Any] = {"id": ayrshare_post_id.strip()}
    if platforms:
        norm = normalize_platforms(platforms)
        if norm:
            payload["platforms"] = norm

    headers: Dict[str, str] = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    pk = (profile_key or "").strip()
    if pk:
        headers["Profile-Key"] = pk
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                AYRSHARE_ANALYTICS_POST_URL,
                json=payload,
                headers=headers,
            )
    except httpx.RequestError as e:
        return {
            "ok": False,
            "status_code": 0,
            "body": {"error": "request_error", "detail": str(e)},
        }

    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}

    ok = 200 <= resp.status_code < 300
    if isinstance(data, dict) and data.get("status") == "error":
        ok = False

    return {"ok": ok, "status_code": resp.status_code, "body": data}
