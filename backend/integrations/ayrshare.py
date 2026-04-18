import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Union

import httpx

from backend.core.logger import get_logger, log_event

# Official API host (matches publish docs; app.* may 404 or redirect for some keys)
AYRSHARE_POST_URL = "https://api.ayrshare.com/api/post"
AYRSHARE_ANALYTICS_POST_URL = "https://api.ayrshare.com/api/analytics/post"

log = get_logger("brokerai.ayrshare")


def _ayrshare_api_key_after_quote_bearer_strip(value: str) -> str:
    """Strip BOM, outer quotes, and ``Bearer `` prefix only (before whitespace collapse)."""
    s = (value or "").replace("\ufeff", "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        s = s[1:-1].strip()
    if s.lower().startswith("bearer "):
        s = s[7:].strip()
    return s


def normalize_ayrshare_api_key(value: str) -> str:
    """
    Normalize AYRSHARE_API_KEY from env (Render/dashboard pastes).

    Handles BOM, outer quotes, accidental ``Bearer `` prefix, and internal line breaks /
    spaces (PDF or email copies often split the key across lines).
    """
    s = _ayrshare_api_key_after_quote_bearer_strip(value)
    # Ayrshare keys are a single token; joining split() removes newlines/tabs/spaces.
    return "".join(s.split())


def ayrshare_connect_env_snapshot() -> Dict[str, Any]:
    """Non-secret flags for /health — verify Render injected Ayrshare vars (no key material)."""
    from pathlib import Path

    raw = os.getenv("AYRSHARE_API_KEY", "") or ""
    inter = _ayrshare_api_key_after_quote_bearer_strip(raw)
    norm = normalize_ayrshare_api_key(raw)
    whitespace_stripped_from_key = bool(norm and inter != norm)
    pk = (os.getenv("AYRSHARE_PRIVATE_KEY", "") or "").strip()
    ppath = (os.getenv("AYRSHARE_PRIVATE_KEY_PATH", "") or "").strip()
    domain = (os.getenv("AYRSHARE_SSO_DOMAIN", "") or "").strip()
    path_exists = bool(ppath and Path(ppath).expanduser().is_file())
    return {
        "api_key_configured": bool(norm),
        "api_key_length": len(norm),
        "api_key_had_whitespace_removed": whitespace_stripped_from_key,
        "sso_domain_configured": bool(domain),
        "private_key_inline_configured": bool(pk),
        "private_key_path_configured": bool(ppath),
        "private_key_path_file_exists": path_exists,
    }


def _ayrshare_single_account_publish() -> bool:
    """Primary-account POST /api/post (no Profile-Key). Set AYRSHARE_SINGLE_ACCOUNT_PUBLISH=true for local testing."""
    return os.getenv("AYRSHARE_SINGLE_ACCOUNT_PUBLISH", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


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


def slug_from_ayrshare_account_label(raw: str) -> Optional[str]:
    """Map Ayrshare `activeSocialAccounts` entry to a canonical platform slug, or None if unknown."""
    key = str(raw).strip().lower().replace(" ", "").replace("_", "")
    slug = _PLATFORM_ALIASES.get(key)
    if slug is None and key in ("linkedin", "facebook", "instagram"):
        slug = key
    return slug


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


def _post_ids_list_has_success(entries: Any) -> bool:
    """True if Ayrshare postIds array contains at least one non-error published id."""
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("status") or "").lower() == "error":
            continue
        pid = entry.get("id")
        if isinstance(pid, str) and pid.strip():
            return True
    return False


def _has_any_successful_publish(data: Dict[str, Any]) -> bool:
    """
    Ayrshare sometimes returns top-level status \"error\" while one or more networks
    succeeded (e.g. Facebook ok, Instagram not linked). Treat as partial success.
    """
    posts_inner = data.get("posts")
    if isinstance(posts_inner, list):
        for block in posts_inner:
            if isinstance(block, dict) and _post_ids_list_has_success(block.get("postIds")):
                return True
    return _post_ids_list_has_success(data.get("postIds"))


async def publish_post(
    caption: str,
    platforms: List[str],
    media_urls: Optional[List[str]] = None,
    profile_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    POST to Ayrshare. Returns a dict with ok (bool), status_code, and body (parsed or raw).
    Business Plan user profiles: send Profile-Key. Primary account only: set
    AYRSHARE_SINGLE_ACCOUNT_PUBLISH=true and omit Profile-Key (matches Ayrshare single-profile POST).
    """
    api_key = normalize_ayrshare_api_key(os.getenv("AYRSHARE_API_KEY", ""))
    if not api_key:
        return {
            "ok": False,
            "status_code": 0,
            "body": {"error": "missing_env", "detail": "AYRSHARE_API_KEY is not set"},
        }

    single_primary = _ayrshare_single_account_publish()
    pk = (profile_key or "").strip()
    if not pk and not single_primary:
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

    headers: Dict[str, str] = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if single_primary:
        log.warning(
            "ayrshare_publish_primary_account_no_profile_key platforms=%s",
            normalized,
        )
    elif pk:
        headers["Profile-Key"] = pk
    # Retry transient failures: network errors, timeouts, 429, 5xx. Exponential backoff.
    max_attempts = 3
    resp = None
    last_exc: Optional[Exception] = None
    _ayr_t0 = time.perf_counter()
    attempts_used = 0
    for attempt in range(1, max_attempts + 1):
        attempts_used = attempt
        _attempt_t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(
                    AYRSHARE_POST_URL,
                    json=payload,
                    headers=headers,
                )
            log_event(
                "publish.ayrshare.attempt",
                attempt=attempt,
                status_code=resp.status_code,
                duration_ms=int((time.perf_counter() - _attempt_t0) * 1000),
                platforms=",".join(normalized),
            )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as e:
            last_exc = e
            log_event(
                "publish.ayrshare.attempt",
                level=logging.WARNING,
                attempt=attempt,
                status="network_error",
                error=type(e).__name__,
                duration_ms=int((time.perf_counter() - _attempt_t0) * 1000),
            )
            log.warning(
                "ayrshare_transient_network_error attempt=%s/%s err=%s",
                attempt, max_attempts, e,
            )
            if attempt < max_attempts:
                await asyncio.sleep(0.75 * (2 ** (attempt - 1)))
                continue
            return {
                "ok": False,
                "status_code": 0,
                "body": {
                    "error": "network_error",
                    "detail": f"Could not reach Ayrshare: {e}",
                    "user_message": "Network issue reaching the publisher. Please try again.",
                    "retryable": True,
                },
            }
        except httpx.RequestError as e:
            log.exception("ayrshare_request_error")
            return {
                "ok": False,
                "status_code": 0,
                "body": {
                    "error": "request_error",
                    "detail": str(e),
                    "user_message": "Publishing failed unexpectedly. Please try again.",
                    "retryable": True,
                },
            }

        # Retry on rate limit / server error
        if resp is not None and (resp.status_code == 429 or 500 <= resp.status_code < 600):
            log.warning(
                "ayrshare_retryable_status attempt=%s/%s status=%s",
                attempt, max_attempts, resp.status_code,
            )
            if attempt < max_attempts:
                await asyncio.sleep(0.75 * (2 ** (attempt - 1)))
                continue
        break

    if resp is None:
        return {
            "ok": False,
            "status_code": 0,
            "body": {
                "error": "network_error",
                "detail": str(last_exc or "Unknown network failure"),
                "user_message": "Could not reach the publisher. Please try again.",
                "retryable": True,
            },
        }

    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}

    ok = 200 <= resp.status_code < 300
    if isinstance(data, dict):
        st = str(data.get("status") or "").lower()
        if st == "error":
            if _has_any_successful_publish(data):
                ok = True
                data.setdefault(
                    "partial_success",
                    True,
                )
                data.setdefault(
                    "user_message",
                    "Published to linked networks. Platforms that are not connected were skipped.",
                )
            else:
                ok = False
        elif st == "success" or st == "scheduled":
            # Partial platform failures may still return 200 with errors[]
            errs = data.get("errors")
            if isinstance(errs, list) and errs:
                post_ids = data.get("postIds")
                nested = data.get("posts")
                has_ok = isinstance(post_ids, list) and len(post_ids) > 0
                if isinstance(nested, list) and nested:
                    for block in nested:
                        if not isinstance(block, dict):
                            continue
                        if str(block.get("status") or "").lower() in (
                            "success",
                            "scheduled",
                        ):
                            has_ok = True
                            break
                        pids = block.get("postIds")
                        if isinstance(pids, list) and len(pids) > 0:
                            has_ok = True
                            break
                if not has_ok:
                    ok = False
        posts = data.get("posts")
        if isinstance(posts, list) and posts:
            first = posts[0]
            if isinstance(first, dict) and str(first.get("status") or "").lower() == "error":
                inner_errs = first.get("errors")
                inner_pids = first.get("postIds")
                if (
                    isinstance(inner_errs, list)
                    and inner_errs
                    and not _post_ids_list_has_success(inner_pids)
                ):
                    ok = False

    # Annotate the body with a UI-friendly classification so the frontend
    # can render actionable messages without parsing raw Ayrshare payloads.
    if not ok and isinstance(data, dict):
        classification = _classify_ayrshare_failure(resp.status_code, data, normalized)
        # Do not overwrite values already set (e.g. by the retry layer).
        for k, v in classification.items():
            data.setdefault(k, v)

    return {"ok": ok, "status_code": resp.status_code, "body": data}


def _classify_ayrshare_failure(
    status_code: int, body: Dict[str, Any], platforms: List[str]
) -> Dict[str, Any]:
    """Turn an Ayrshare error payload into a UI-friendly classification.

    Returns keys:
      error         — stable machine code (e.g. "not_connected", "rate_limited")
      user_message  — short human-readable message for UI toasts
      retryable     — bool, whether a retry may help
      action        — optional UI action hint: "reconnect" | "retry" | "contact_support"
    """
    msg_blob = json.dumps(body, default=str).lower()

    # Auth / key problems
    if status_code in (401, 403):
        return {
            "error": "auth_failed",
            "user_message": "Publishing credentials were rejected. Please reconnect.",
            "retryable": False,
            "action": "reconnect",
        }

    # Rate limit / quota
    if status_code == 429 or "rate limit" in msg_blob or "quota" in msg_blob:
        return {
            "error": "rate_limited",
            "user_message": "Publishing quota hit. Please try again in a few minutes.",
            "retryable": True,
            "action": "retry",
        }

    # Account not linked / expired token / needs reconnect
    reconnect_signals = (
        "not connected",
        "no social accounts",
        "no accounts",
        "social network not linked",
        "link your",
        "please link",
        "token expired",
        "reauthorize",
        "re-authorize",
        "re-authenticate",
        "reconnect",
    )
    if any(s in msg_blob for s in reconnect_signals):
        hint = ""
        for p in platforms:
            if p in msg_blob:
                hint = f" Please reconnect {p.title()}."
                break
        return {
            "error": "not_connected",
            "user_message": f"A social account needs to be reconnected.{hint}".strip(),
            "retryable": False,
            "action": "reconnect",
        }

    # Validation / caption issues
    if status_code == 400 or "invalid" in msg_blob or "validation" in msg_blob:
        return {
            "error": "invalid_post",
            "user_message": "This post was rejected by the platform. Please edit and try again.",
            "retryable": False,
            "action": "edit",
        }

    # 5xx (should mostly be handled by retry layer, but classify last attempt)
    if 500 <= status_code < 600:
        return {
            "error": "provider_unavailable",
            "user_message": "Publisher is temporarily unavailable. Please try again.",
            "retryable": True,
            "action": "retry",
        }

    return {
        "error": "publish_failed",
        "user_message": "Publishing failed. Please try again.",
        "retryable": True,
        "action": "retry",
    }


def platform_response_json(result: Dict[str, Any]) -> str:
    return json.dumps(result, default=str)


def _first_success_platform_from_post_ids(items: Any) -> tuple[str, str]:
    """From postIds / nested postIds list, return (provider_post_id, platform_slug)."""
    if not isinstance(items, list):
        return "", ""
    for entry in items:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("status") or "").lower() == "error":
            continue
        pid = entry.get("id")
        sid = pid.strip() if isinstance(pid, str) and pid.strip() else ""
        raw_p = entry.get("platform")
        if isinstance(raw_p, str) and raw_p.strip():
            norm = normalize_platforms([raw_p.strip()])
            if norm:
                return sid, norm[0]
        if sid:
            return sid, "facebook"
    return "", ""


def extract_ayrshare_publish_metadata(body: Any) -> tuple[str, str]:
    """
    From a successful Ayrshare publish JSON body, return (ayrshare_or_provider_post_id, primary_platform_slug).
    Prefer top-level `id` (Ayrshare post id) when present; else first successful platform post id.
    """
    if not isinstance(body, dict):
        return "", ""
    social_id = ""
    plat = ""
    top_id = body.get("id")
    if isinstance(top_id, str) and top_id.strip():
        social_id = top_id.strip()

    post_ids = body.get("postIds")
    sid2, plat2 = _first_success_platform_from_post_ids(post_ids)
    if plat2:
        plat = plat2
    if sid2 and not social_id:
        social_id = sid2

    posts = body.get("posts")
    if isinstance(posts, list) and posts:
        first = posts[0]
        if isinstance(first, dict):
            inner_id = first.get("id")
            if isinstance(inner_id, str) and inner_id.strip():
                social_id = inner_id.strip()
            inner_pids = first.get("postIds")
            sid3, plat3 = _first_success_platform_from_post_ids(inner_pids)
            if plat3:
                plat = plat3
            if sid3 and not social_id:
                social_id = sid3
            if not plat:
                raw_p = first.get("platform")
                if isinstance(raw_p, str) and raw_p.strip():
                    norm = normalize_platforms([raw_p.strip()])
                    if norm:
                        plat = norm[0]
    if not plat:
        for key in ("facebook", "instagram", "linkedin", "twitter", "tiktok", "youtube"):
            block = body.get(key)
            if isinstance(block, dict) and block.get("status") not in ("error", "failed"):
                norm = normalize_platforms([key])
                plat = norm[0] if norm else key
                if not social_id:
                    pid = block.get("id") or block.get("postId")
                    if isinstance(pid, str) and pid.strip():
                        social_id = pid.strip()
                break
    if not plat:
        plat = "facebook"
    return social_id, plat


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
    api_key = normalize_ayrshare_api_key(os.getenv("AYRSHARE_API_KEY", ""))
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
