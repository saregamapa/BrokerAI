"""
Ayrshare helpers used by publishing (linked-platform checks via GET /api/user).

Official API host: https://api.ayrshare.com
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import httpx

from backend.core.logger import get_logger
from backend.integrations.ayrshare import normalize_ayrshare_api_key, slug_from_ayrshare_account_label

log = get_logger("brokerai.ayrshare_service")

AYRSHARE_API_USER = "https://api.ayrshare.com/api/user"

# All three must be linked before campaign creation / publishing.
REQUIRED_LINKED_SOCIAL_PLATFORMS = frozenset({"facebook", "instagram", "linkedin"})

# GET /api/user may return activeSocialAccounts=[] briefly after OAuth; retry before failing verify.
_USER_PLATFORM_BLOCKS = (
    "facebook",
    "instagram",
    "linkedin",
    "twitter",
    "tiktok",
    "youtube",
    "pinterest",
    "threads",
)


def _api_key() -> str:
    return normalize_ayrshare_api_key(os.getenv("AYRSHARE_API_KEY", ""))


def _coerce_ayrshare_platform_slug(raw: str) -> str:
    """Normalize an Ayrshare platform label to a lowercase slug (unknown labels kept as normalized string)."""
    s = str(raw).strip()
    if not s:
        return ""
    mapped = slug_from_ayrshare_account_label(s)
    if mapped:
        return mapped
    return s.lower().replace(" ", "").replace("_", "")


def _platform_slugs_from_active_social_entries(entries: List[Any]) -> List[str]:
    """Parse activeSocialAccounts / activeSocialNetworks list entries (strings or {platform: ...} objects)."""
    out: List[str] = []
    for x in entries:
        if x is None:
            continue
        if isinstance(x, str):
            slug = _coerce_ayrshare_platform_slug(x)
            if slug:
                out.append(slug)
        elif isinstance(x, dict):
            p = x.get("platform") or x.get("network") or x.get("name")
            if isinstance(p, str) and p.strip():
                slug = _coerce_ayrshare_platform_slug(p)
                if slug:
                    out.append(slug)
    return list(dict.fromkeys(out))


def _parse_display_names_platforms(data: Dict[str, Any]) -> List[str]:
    """Ayrshare returns linked account rows under displayNames[].platform (see profile-details docs)."""
    dn = data.get("displayNames")
    if not isinstance(dn, list) or not dn:
        return []
    out: List[str] = []
    for item in dn:
        if not isinstance(item, dict):
            continue
        p = item.get("platform")
        if isinstance(p, str) and p.strip():
            slug = _coerce_ayrshare_platform_slug(p)
            if slug:
                out.append(slug)
    return list(dict.fromkeys(out))


def parse_profile_linked_platforms(profile: Dict[str, Any]) -> List[str]:
    """Linked platform slugs from one object in GET /api/profiles `profiles` array."""
    raw = profile.get("activeSocialAccounts")
    if isinstance(raw, list) and raw:
        return _platform_slugs_from_active_social_entries(raw)
    sh = profile.get("socialHealth")
    if isinstance(sh, dict):
        linked: List[str] = []
        for plat_key, meta in sh.items():
            if not isinstance(plat_key, str) or not isinstance(meta, dict):
                continue
            if meta.get("linked") is True:
                slug = _coerce_ayrshare_platform_slug(plat_key)
                if slug:
                    linked.append(slug)
        if linked:
            return list(dict.fromkeys(linked))
    return []


def _ayrshare_get_user_payload(
    *, api_key: str, profile_key: str, timeout_sec: float = 12.0
) -> Optional[Dict[str, Any]]:
    pk = profile_key.strip()
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            resp = client.get(
                AYRSHARE_API_USER,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Profile-Key": pk,
                },
            )
    except Exception as e:
        log.warning("Ayrshare GET /user failed profile_key_prefix=%s err=%s", pk[:8], e)
        return None

    try:
        data = resp.json()
    except Exception:
        return None

    if resp.status_code >= 400 or not isinstance(data, dict):
        log.warning(
            "Ayrshare GET /user HTTP %s for profile_key prefix %s",
            resp.status_code,
            pk[:8],
        )
        return None

    if data.get("status") == "error":
        log.warning("Ayrshare GET /user error: %s", data.get("message"))
        return None

    return data


def _parse_active_social_accounts_from_user_payload(data: Dict[str, Any]) -> List[str]:
    """Derive linked platform labels from GET /api/user JSON (handles alternate shapes)."""
    raw = data.get("activeSocialAccounts")
    if isinstance(raw, list) and raw:
        acc = _platform_slugs_from_active_social_entries(raw)
        if acc:
            return acc

    raw2 = data.get("activeSocialNetworks")
    if isinstance(raw2, list) and raw2:
        acc2 = _platform_slugs_from_active_social_entries(raw2)
        if acc2:
            return acc2

    dn_slugs = _parse_display_names_platforms(data)
    if dn_slugs:
        return dn_slugs

    found: List[str] = []
    for plat in _USER_PLATFORM_BLOCKS:
        block = data.get(plat)
        if isinstance(block, dict):
            if block.get("active") is True or block.get("linked") is True:
                found.append(plat)
                continue
            pid = str(block.get("id") or block.get("userId") or "").strip()
            handle = str(
                block.get("username")
                or block.get("userName")
                or block.get("handle")
                or block.get("screenName")
                or ""
            ).strip()
            disp = str(block.get("displayName") or block.get("name") or "").strip()
            if pid and (handle or disp):
                found.append(plat)
        elif isinstance(block, str) and block.strip():
            found.append(plat)

    return list(dict.fromkeys(found))


def fetch_active_social_accounts(profile_key: str, *, quick: bool = False) -> Optional[List[str]]:
    """
    GET /api/user with Profile-Key. Returns linked platform ids, or None on transport/API failure.

    After OAuth, Ayrshare may briefly return an empty activeSocialAccounts list; we retry with
    backoff (AYRSHARE_OAUTH_VERIFY_ATTEMPTS / AYRSHARE_OAUTH_VERIFY_DELAY_SEC).
    """
    key = _api_key()
    pk = profile_key.strip()
    if not key or not pk:
        log.warning(
            "fetch_active_social_accounts skipped: api_key_present=%s profile_key_present=%s",
            bool(key),
            bool(pk),
        )
        return None

    try:
        max_attempts = int((os.getenv("AYRSHARE_OAUTH_VERIFY_ATTEMPTS") or "5").strip())
    except ValueError:
        max_attempts = 5
    max_attempts = max(1, min(15, max_attempts))
    if quick:
        max_attempts = 1

    try:
        delay_sec = float((os.getenv("AYRSHARE_OAUTH_VERIFY_DELAY_SEC") or "0.85").strip())
    except ValueError:
        delay_sec = 0.85
    delay_sec = max(0.05, min(5.0, delay_sec))

    user_timeout = 5.0 if quick else 12.0

    for attempt in range(max_attempts):
        data = _ayrshare_get_user_payload(
            api_key=key, profile_key=pk, timeout_sec=user_timeout
        )
        if data is None:
            return None
        accounts = _parse_active_social_accounts_from_user_payload(data)
        if accounts:
            log.info(
                "Ayrshare GET /user profile_key_prefix=%s active_accounts=%s attempts=%s",
                pk[:8],
                accounts,
                attempt + 1,
            )
            return accounts
        raw = data.get("activeSocialAccounts")
        if not isinstance(raw, list):
            log.warning(
                "Ayrshare GET /user unexpected activeSocialAccounts for profile_key prefix %s — data keys: %s",
                pk[:8],
                list(data.keys()),
            )
        if attempt < max_attempts - 1:
            log.info(
                "ayrshare_oauth_verify_retry profile_key_prefix=%s attempt=%s/%s delay=%ss",
                pk[:8],
                attempt + 1,
                max_attempts,
                delay_sec,
            )
            time.sleep(delay_sec)

    log.warning(
        "Ayrshare GET /user no linked accounts after %s attempts prefix=%s",
        max_attempts,
        pk[:8],
    )
    return []


def fetch_user_profile_json(profile_key: str, *, quick: bool = False) -> Optional[Dict[str, Any]]:
    """
    GET /api/user with retries; returns the JSON body even when activeSocialAccounts is still empty
    (needed so sync can read displayNames / per-platform blocks after OAuth).
    """
    key = _api_key()
    pk = profile_key.strip()
    if not key or not pk:
        return None
    try:
        max_attempts = int((os.getenv("AYRSHARE_OAUTH_VERIFY_ATTEMPTS") or "5").strip())
    except ValueError:
        max_attempts = 5
    max_attempts = max(1, min(15, max_attempts))
    if quick:
        max_attempts = 1
    try:
        delay_sec = float((os.getenv("AYRSHARE_OAUTH_VERIFY_DELAY_SEC") or "0.85").strip())
    except ValueError:
        delay_sec = 0.85
    delay_sec = max(0.05, min(5.0, delay_sec))
    user_timeout = 5.0 if quick else 12.0
    last: Optional[Dict[str, Any]] = None
    for attempt in range(max_attempts):
        data = _ayrshare_get_user_payload(api_key=key, profile_key=pk, timeout_sec=user_timeout)
        if data is None:
            return None
        last = data
        raw = data.get("activeSocialAccounts")
        has_display = isinstance(data.get("displayNames"), list) and len(data.get("displayNames") or []) > 0
        has_accounts = isinstance(raw, list) and len(raw) > 0
        if has_accounts or has_display:
            return data
        if attempt < max_attempts - 1:
            time.sleep(delay_sec)
    return last


def linked_social_slugs(active_accounts: Optional[List[str]]) -> set[str]:
    """Canonical slugs for FB / IG / LI found in Ayrshare activeSocialAccounts."""
    slugs: set[str] = set()
    if not active_accounts:
        return slugs
    for x in active_accounts:
        slug = slug_from_ayrshare_account_label(str(x))
        if slug:
            slugs.add(slug)
    return slugs


def is_social_connection_satisfied(active_accounts: Optional[List[str]]) -> bool:
    """
    True when enough of Facebook / Instagram / LinkedIn are linked for this app.

    Default is one linked network. Set AYRSHARE_MIN_LINKED_PLATFORMS=2 or 3 if you require
    more networks before treating the workspace as fully connected.
    """
    linked = linked_social_slugs(active_accounts) & REQUIRED_LINKED_SOCIAL_PLATFORMS
    raw = (os.getenv("AYRSHARE_MIN_LINKED_PLATFORMS") or "1").strip()
    try:
        need = int(raw)
    except ValueError:
        need = 3
    need = max(1, min(3, need))
    satisfied = len(linked) >= need
    log.debug(
        "is_social_connection_satisfied linked=%s need=%s satisfied=%s",
        sorted(linked),
        need,
        satisfied,
    )
    return satisfied


def has_all_target_platforms_linked(active_accounts: Optional[List[str]]) -> bool:
    """Backward-compatible name: satisfied when MIN_LINKED_PLATFORMS threshold is met (default 1)."""
    return is_social_connection_satisfied(active_accounts)
