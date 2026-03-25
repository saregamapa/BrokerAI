"""
Ayrshare Business Plan: user profiles, JWT SSO connect URLs, and linked-account sync.

Official API hosts use https://api.ayrshare.com (not app.ayrshare.com for REST).
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from backend.core.logger import get_logger
from backend.integrations.ayrshare import slug_from_ayrshare_account_label

log = get_logger("brokerai.ayrshare_service")

AYRSHARE_API_CREATE_PROFILE = "https://api.ayrshare.com/api/profiles"
AYRSHARE_API_GENERATE_JWT = "https://api.ayrshare.com/api/profiles/generateJWT"
AYRSHARE_API_USER = "https://api.ayrshare.com/api/user"

# All three must be linked before campaign creation / publishing.
REQUIRED_LINKED_SOCIAL_PLATFORMS = frozenset({"facebook", "instagram", "linkedin"})


class AyrshareServiceError(Exception):
    """Raised when configuration is missing or Ayrshare returns an error."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _api_key() -> str:
    return os.getenv("AYRSHARE_API_KEY", "").strip()


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
        return b64_val.replace("\n", "").replace(" ", ""), True

    raw = os.getenv("AYRSHARE_PRIVATE_KEY", "").strip()
    if raw:
        pem = _normalize_private_key_pem(raw.replace("\\n", "\n"))
        if b64_flag:
            return base64.b64encode(pem.encode("utf-8")).decode("ascii"), True
        return pem, False

    key_path = os.getenv("AYRSHARE_PRIVATE_KEY_PATH", "").strip()
    if key_path:
        p = Path(key_path).expanduser()
        if p.is_file():
            pem = _normalize_private_key_pem(p.read_text(encoding="utf-8"))
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
        with httpx.Client(timeout=45.0) as client:
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
    return client.post(
        AYRSHARE_API_GENERATE_JWT,
        data=form,
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
        raise AyrshareServiceError("AYRSHARE_API_KEY is not configured", status_code=503)
    if not domain or not private_key:
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
        with httpx.Client(timeout=45.0) as client:
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
        return url

    # Fallback: if JSON failed but form not tried yet, retry with form (raw PEM only).
    if mode == "json" and not pk_is_b64:
        try:
            with httpx.Client(timeout=45.0) as client:
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


def fetch_active_social_accounts(profile_key: str) -> Optional[List[str]]:
    """
    GET /api/user with Profile-Key. Returns activeSocialAccounts list, or None on failure.
    """
    key = _api_key()
    pk = profile_key.strip()
    if not key or not pk:
        return None
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                AYRSHARE_API_USER,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Profile-Key": pk,
                },
            )
    except httpx.RequestError as e:
        log.warning("Ayrshare GET /user failed profile_key=%s err=%s", pk[:8], e)
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

    raw = data.get("activeSocialAccounts")
    if not isinstance(raw, list):
        return []
    return [str(x).strip().lower() for x in raw if x]


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

    Default requires all three. Set AYRSHARE_MIN_LINKED_PLATFORMS=1 or 2 on Render if you want
    a softer gate while onboarding (wizard + publish still use whatever platforms are linked).
    """
    linked = linked_social_slugs(active_accounts) & REQUIRED_LINKED_SOCIAL_PLATFORMS
    raw = (os.getenv("AYRSHARE_MIN_LINKED_PLATFORMS") or "3").strip()
    try:
        need = int(raw)
    except ValueError:
        need = 3
    need = max(1, min(3, need))
    return len(linked) >= need


def has_all_target_platforms_linked(active_accounts: Optional[List[str]]) -> bool:
    """Backward-compatible name: satisfied when MIN_LINKED_PLATFORMS threshold is met (default 3)."""
    return is_social_connection_satisfied(active_accounts)
