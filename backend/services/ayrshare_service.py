"""
Ayrshare Business Plan: user profiles, JWT SSO connect URLs, and linked-account sync.

Official API hosts use https://api.ayrshare.com (not app.ayrshare.com for REST).
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx

from backend.core.logger import get_logger
from backend.integrations.ayrshare import normalize_ayrshare_api_key, slug_from_ayrshare_account_label

log = get_logger("brokerai.ayrshare_service")

AYRSHARE_API_CREATE_PROFILE = "https://api.ayrshare.com/api/profiles"
AYRSHARE_API_GENERATE_JWT = "https://api.ayrshare.com/api/profiles/generateJWT"
AYRSHARE_API_USER = "https://api.ayrshare.com/api/user"
AYRSHARE_API_GET_PROFILES = "https://api.ayrshare.com/api/profiles"

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


class AyrshareServiceError(Exception):
    """Raised when configuration is missing or Ayrshare returns an error."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _api_key() -> str:
    return normalize_ayrshare_api_key(os.getenv("AYRSHARE_API_KEY", ""))


def format_ayrshare_operator_hint(message: str) -> str:
    """
    Turn Ayrshare's generic auth errors into deploy guidance (shown in API/UI detail).
    """
    m = (message or "").strip()
    low = m.lower()
    # Ayrshare returns this when Authorization uses a User Profile Key instead of the Primary API Key.
    if "profile key" in low and "api key" in low and (
        "cannot use" in low or "use a profile" in low or "profile key as" in low
    ):
        return (
            "AYRSHARE_API_KEY must be the Primary (account) API Key, not a User Profile Key. "
            "In the Ayrshare dashboard, switch to your Primary Profile (top/profile switcher), "
            "then open Social Media API → API Key in the left nav and copy that value into "
            "AYRSHARE_API_KEY on Render. Profile Keys are only sent as the Profile-Key header "
            "for end users; BrokerAI stores those in the database after profile creation. "
            "https://www.ayrshare.com/docs/apis/overview#profile-key-format"
        )
    if "api key not valid" in low or ("authorization" in low and "bearer" in low):
        return (
            "Ayrshare rejected the server API key. On Render: Web service → Environment → "
            "AYRSHARE_API_KEY = Primary API Key (Social Media API → API Key) from the same Business "
            "account as AYRSHARE_SSO_DOMAIN and your private key package. Not a Profile Key; no "
            "'Bearer ' prefix. GET /health: ayrshare.api_key_length should match your key length; "
            "if api_key_had_whitespace_removed is true, the paste had line breaks (now fixed in code). "
            "If length looks correct but this error remains, the key is wrong or from another Ayrshare "
            "account than the SSO package—regenerate the key in Ayrshare and paste again, then redeploy."
        )
    if m == "AYRSHARE_API_KEY is not configured":
        return (
            "AYRSHARE_API_KEY is not set on the server. Add it under Render → Environment for this service, "
            "then redeploy."
        )
    return m


def _normalize_private_key_pem(pem: str) -> str:
    """
    Ayrshare rejects PEMs with stray whitespace (see JWT error code 189).
    Preserve inner newlines; trim outer space and unify line endings.
    """
    s = pem.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not s:
        return ""
    # Strip BOM / invisible chars at start of first line
    s = s.lstrip("\ufeff")
    return s + ("\n" if not s.endswith("\n") else "")


def _load_private_key_for_jwt() -> Tuple[str, bool]:
    """
    Returns (private_key_string, use_base64_flag) for generateJWT body.
    If use_base64_flag is True, send as base64-encoded PEM with privateKeyBase64: true (Ayrshare docs).
    """
    b64_flag = os.getenv("AYRSHARE_PRIVATE_KEY_BASE64", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    b64_val = os.getenv("AYRSHARE_PRIVATE_KEY_BASE64_VALUE", "").strip()
    if b64_val:
        # Decode the base64 value back to raw PEM, then normalize.
        # Ayrshare's generateJWT expects the plain PEM string in "privateKey" —
        # there is no "privateKeyBase64" flag in their API.
        try:
            pem = _normalize_private_key_pem(
                base64.b64decode(b64_val.replace("\n", "").replace(" ", "")).decode("utf-8")
            )
        except Exception as exc:
            log.error("AYRSHARE_PRIVATE_KEY_BASE64_VALUE decode failed: %s", exc)
            return "", False
        return pem, False

    key_path = os.getenv("AYRSHARE_PRIVATE_KEY_PATH", "").strip()
    if key_path:
        p = Path(key_path).expanduser()
        if p.is_file():
            pem = _normalize_private_key_pem(p.read_text(encoding="utf-8"))
            if b64_flag:
                return base64.b64encode(pem.encode("utf-8")).decode("ascii"), True
            return pem, False

    raw = os.getenv("AYRSHARE_PRIVATE_KEY", "").strip()
    if raw:
        pem = _normalize_private_key_pem(raw.replace("\\n", "\n"))
        if b64_flag:
            return base64.b64encode(pem.encode("utf-8")).decode("ascii"), True
        return pem, False
    return "", False


def _sso_domain() -> str:
    return os.getenv("AYRSHARE_SSO_DOMAIN", "").strip()


def create_ayrshare_profile(user_id: int, _email: str) -> str:
    """
    Create a User Profile under the primary Ayrshare account.
    Returns profileKey (store per user; required for Profile-Key header on post/user APIs).
    """
    key = _api_key()
    if not key:
        raise AyrshareServiceError("AYRSHARE_API_KEY is not configured", status_code=503)

    title = f"BrokerAI User {user_id}"
    # refId associates profile in Ayrshare with our user id (retrievable in user responses)
    payload: Dict[str, Any] = {
        "title": title,
        "refId": f"brokerai_user_{user_id}",
    }
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                AYRSHARE_API_CREATE_PROFILE,
                json=payload,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
    except httpx.RequestError as e:
        log.exception("Ayrshare create profile request failed user_id=%s", user_id)
        raise AyrshareServiceError(
            f"Ayrshare profile creation failed: {e}", status_code=502
        ) from e

    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}

    if resp.status_code >= 400 or not isinstance(data, dict):
        log.error(
            "Ayrshare create profile HTTP %s user_id=%s body=%s",
            resp.status_code,
            user_id,
            data,
        )
        msg = (
            data.get("message")
            if isinstance(data, dict)
            else "Invalid response from Ayrshare"
        )
        raise AyrshareServiceError(
            str(msg or "Could not create Ayrshare user profile"),
            status_code=502,
        )

    if data.get("status") == "error":
        log.error(
            "Ayrshare create profile error user_id=%s code=%s message=%s",
            user_id,
            data.get("code"),
            data.get("message"),
        )
        raise AyrshareServiceError(
            str(data.get("message") or "Ayrshare profile creation rejected"),
            status_code=502,
        )

    profile_key = data.get("profileKey")
    if not isinstance(profile_key, str) or not profile_key.strip():
        log.error("Ayrshare create profile missing profileKey user_id=%s data=%s", user_id, data)
        raise AyrshareServiceError(
            "Ayrshare did not return a profile key", status_code=502
        )

    log.info(
        "Ayrshare profile created user_id=%s profile_key_prefix=%s",
        user_id,
        profile_key.strip()[:8],
    )
    return profile_key.strip()


def _generate_jwt_request_json(
    client: httpx.Client,
    api_key: str,
    domain: str,
    private_key: str,
    profile_key: str,
    *,
    private_key_is_b64: bool,
    redirect_after_connect: Optional[str],
) -> httpx.Response:
    body: Dict[str, Any] = {
        "domain": domain,
        "privateKey": private_key,
        "profileKey": profile_key.strip(),
        "allowedSocial": ["facebook", "instagram", "linkedin"],
    }
    if private_key_is_b64:
        body["privateKeyBase64"] = True
    if redirect_after_connect and redirect_after_connect.strip():
        body["redirect"] = redirect_after_connect.strip()
    return client.post(
        AYRSHARE_API_GENERATE_JWT,
        json=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )


def _generate_jwt_request_form(
    client: httpx.Client,
    api_key: str,
    domain: str,
    private_key: str,
    profile_key: str,
    *,
    redirect_after_connect: Optional[str],
) -> httpx.Response:
    """Postman-style application/x-www-form-urlencoded (some dashboards use this)."""
    form: List[Tuple[str, str]] = [
        ("domain", domain),
        ("privateKey", private_key),
        ("profileKey", profile_key.strip()),
    ]
    if redirect_after_connect and redirect_after_connect.strip():
        form.append(("redirect", redirect_after_connect.strip()))
    # Optional; Postman samples often omit this — dashboard “Social Networks” applies if unset.
    if os.getenv("AYRSHARE_JWT_FORM_INCLUDE_ALLOWED_SOCIAL", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        for plat in ("facebook", "instagram", "linkedin"):
            form.append(("allowedSocial[]", plat))
    form_encoded = urlencode(form)
    return client.post(
        AYRSHARE_API_GENERATE_JWT,
        content=form_encoded,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )


def generate_social_connect_url(
    profile_key: str,
    redirect_after_connect: Optional[str] = None,
) -> str:
    """
    Returns the JWT SSO URL from Ayrshare (opens profile.ayrshare.com with signed jwt).
    Requires AYRSHARE_SSO_DOMAIN (exact string from onboarding, e.g. id-xxxxx) and private key PEM.

    AYRSHARE_GENERATE_JWT_MODE=json|form — default json per Ayrshare docs; use `form` to match Postman.
    """
    key = _api_key()
    domain = _sso_domain()
    private_key, pk_is_b64 = _load_private_key_for_jwt()
    if not key:
        log.error("generateJWT aborted: AYRSHARE_API_KEY is empty")
        raise AyrshareServiceError("AYRSHARE_API_KEY is not configured", status_code=503)
    if not domain or not private_key:
        log.error(
            "generateJWT aborted: missing config domain=%s private_key_present=%s",
            bool(domain),
            bool(private_key),
        )
        raise AyrshareServiceError(
            "Social SSO is not configured. Set AYRSHARE_SSO_DOMAIN and "
            "AYRSHARE_PRIVATE_KEY (or AYRSHARE_PRIVATE_KEY_PATH) per Ayrshare "
            "Generate JWT documentation.",
            status_code=503,
        )

    mode = os.getenv("AYRSHARE_GENERATE_JWT_MODE", "json").strip().lower()
    if mode not in ("json", "form"):
        mode = "json"

    last_resp: Optional[httpx.Response] = None
    last_data: Any = None

    try:
        with httpx.Client(timeout=20.0) as client:
            if mode == "form":
                if pk_is_b64:
                    log.warning(
                        "AYRSHARE_GENERATE_JWT_MODE=form with base64 key: "
                        "using JSON body instead (form + base64 not supported)."
                    )
                    last_resp = _generate_jwt_request_json(
                        client,
                        key,
                        domain,
                        private_key,
                        profile_key,
                        private_key_is_b64=pk_is_b64,
                        redirect_after_connect=redirect_after_connect,
                    )
                else:
                    last_resp = _generate_jwt_request_form(
                        client,
                        key,
                        domain,
                        private_key,
                        profile_key,
                        redirect_after_connect=redirect_after_connect,
                    )
            else:
                last_resp = _generate_jwt_request_json(
                    client,
                    key,
                    domain,
                    private_key,
                    profile_key,
                    private_key_is_b64=pk_is_b64,
                    redirect_after_connect=redirect_after_connect,
                )
    except httpx.RequestError as e:
        log.exception("Ayrshare generateJWT request failed")
        raise AyrshareServiceError(
            f"Ayrshare SSO URL generation failed: {e}", status_code=502
        ) from e

    try:
        last_data = last_resp.json() if last_resp is not None else None
    except Exception:
        last_data = {"raw": last_resp.text if last_resp else ""}

    def _parse_response(resp: Optional[httpx.Response], data: Any) -> Optional[str]:
        if resp is None or not isinstance(data, dict):
            return None
        if resp.status_code >= 400:
            return None
        if data.get("status") == "error":
            return None
        url = data.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
        return None

    url = _parse_response(last_resp, last_data)
    if url:
        log.info("Ayrshare generateJWT succeeded mode=%s profile_key_prefix=%s", mode, profile_key[:8])
        return url

    # Fallback: if JSON failed but form not tried yet, retry with form (raw PEM only).
    if mode == "json" and not pk_is_b64:
        try:
            with httpx.Client(timeout=20.0) as client:
                last_resp = _generate_jwt_request_form(
                    client,
                    key,
                    domain,
                    private_key,
                    profile_key,
                    redirect_after_connect=redirect_after_connect,
                )
            try:
                last_data = last_resp.json()
            except Exception:
                last_data = {"raw": last_resp.text}
            url = _parse_response(last_resp, last_data)
            if url:
                log.info("Ayrshare generateJWT succeeded via form fallback after JSON attempt")
                return url
        except httpx.RequestError as e:
            log.warning("Ayrshare generateJWT form fallback failed: %s", e)

    if not isinstance(last_data, dict):
        last_data = {}
    log.error(
        "Ayrshare generateJWT HTTP %s body=%s",
        last_resp.status_code if last_resp else 0,
        last_data,
    )
    if last_data.get("status") == "error":
        raise AyrshareServiceError(
            str(last_data.get("message") or "Ayrshare JWT generation failed"),
            status_code=502,
        )
    raise AyrshareServiceError(
        "Could not generate social connect URL from Ayrshare", status_code=502
    )


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


def fetch_profiles_by_ref_id(
    ref_id: str, *, include: Optional[str] = None, timeout_sec: float = 30.0
) -> Optional[List[Dict[str, Any]]]:
    """
    GET /api/profiles filtered by refId.
    Optional `include` (e.g. \"socialHealth\") matches Ayrshare query params for extended rows.
    Returns profile list or None when Ayrshare call fails.
    """
    key = _api_key()
    rid = str(ref_id or "").strip()
    if not key or not rid:
        log.warning(
            "fetch_profiles_by_ref_id skipped: api_key_present=%s ref_id_present=%s",
            bool(key),
            bool(rid),
        )
        return None
    params: Dict[str, Any] = {"refId": rid}
    inc = (include or "").strip()
    if inc:
        params["include"] = inc
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            resp = client.get(
                AYRSHARE_API_GET_PROFILES,
                params=params,
                headers={"Authorization": f"Bearer {key}"},
            )
    except Exception as e:
        log.warning("Ayrshare GET /profiles failed ref_id=%s err=%s", rid, e)
        return None

    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}

    if resp.status_code >= 400 or not isinstance(data, dict):
        log.warning(
            "Ayrshare GET /profiles HTTP %s ref_id=%s body=%s",
            resp.status_code,
            rid,
            data,
        )
        return None

    if data.get("status") == "error":
        log.warning("Ayrshare GET /profiles error ref_id=%s msg=%s", rid, data.get("message"))
        return None

    profiles = data.get("profiles")
    if not isinstance(profiles, list):
        profiles = []
    log.info("Ayrshare GET /profiles ref_id=%s response=%s", rid, data)
    return [p for p in profiles if isinstance(p, dict)]


def fetch_linked_platforms_via_ref_id(
    ref_id: str, *, timeout_sec: float = 30.0
) -> Optional[List[str]]:
    """
    When GET /api/user is empty, Ayrshare may still report links on the Business profile row.
    Uses refId (same value as create profile) + include=socialHealth as a secondary source.
    """
    profiles = fetch_profiles_by_ref_id(
        ref_id, include="socialHealth", timeout_sec=timeout_sec
    )
    if profiles is None:
        return None
    merged: List[str] = []
    for p in profiles:
        merged.extend(parse_profile_linked_platforms(p))
    return list(dict.fromkeys(merged))


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
