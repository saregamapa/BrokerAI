"""
Ayrshare Business Plan: user profiles, JWT SSO connect URLs, and linked-account sync.

Official API hosts use https://api.ayrshare.com (not app.ayrshare.com for REST).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def _load_private_key_pem() -> str:
    """RSA private key PEM for POST /profiles/generateJWT (Business Plan)."""
    raw = os.getenv("AYRSHARE_PRIVATE_KEY", "").strip()
    if raw:
        # Allow single-line .env with literal \n
        return raw.replace("\\n", "\n").strip()
    key_path = os.getenv("AYRSHARE_PRIVATE_KEY_PATH", "").strip()
    if key_path:
        p = Path(key_path).expanduser()
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return ""


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


def generate_social_connect_url(
    profile_key: str,
    redirect_after_connect: Optional[str] = None,
) -> str:
    """
    Returns the JWT SSO URL from Ayrshare (opens profile.ayrshare.com with signed jwt).
    Requires AYRSHARE_SSO_DOMAIN and private key (PEM) from your Business integration package.
    """
    key = _api_key()
    domain = _sso_domain()
    private_key = _load_private_key_pem()
    if not key:
        raise AyrshareServiceError("AYRSHARE_API_KEY is not configured", status_code=503)
    if not domain or not private_key:
        raise AyrshareServiceError(
            "Social SSO is not configured. Set AYRSHARE_SSO_DOMAIN and "
            "AYRSHARE_PRIVATE_KEY (or AYRSHARE_PRIVATE_KEY_PATH) per Ayrshare "
            "Generate JWT documentation.",
            status_code=503,
        )

    body: Dict[str, Any] = {
        "domain": domain,
        "privateKey": private_key,
        "profileKey": profile_key.strip(),
        "allowedSocial": ["facebook", "instagram", "linkedin"],
    }
    if redirect_after_connect and redirect_after_connect.strip():
        body["redirect"] = redirect_after_connect.strip()

    try:
        with httpx.Client(timeout=45.0) as client:
            resp = client.post(
                AYRSHARE_API_GENERATE_JWT,
                json=body,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
    except httpx.RequestError as e:
        log.exception("Ayrshare generateJWT request failed")
        raise AyrshareServiceError(
            f"Ayrshare SSO URL generation failed: {e}", status_code=502
        ) from e

    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}

    if resp.status_code >= 400 or not isinstance(data, dict):
        log.error("Ayrshare generateJWT HTTP %s body=%s", resp.status_code, data)
        raise AyrshareServiceError(
            "Could not generate social connect URL from Ayrshare", status_code=502
        )

    if data.get("status") == "error":
        log.error(
            "Ayrshare generateJWT error code=%s message=%s",
            data.get("code"),
            data.get("message"),
        )
        raise AyrshareServiceError(
            str(data.get("message") or "Ayrshare JWT generation failed"),
            status_code=502,
        )

    url = data.get("url")
    if not isinstance(url, str) or not url.strip():
        log.error("Ayrshare generateJWT missing url in response: %s", data)
        raise AyrshareServiceError(
            "Ayrshare did not return a connect URL", status_code=502
        )
    return url.strip()


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


def has_all_target_platforms_linked(active_accounts: Optional[List[str]]) -> bool:
    """True when Facebook, Instagram, and LinkedIn are all linked on the Ayrshare profile."""
    if not active_accounts:
        return False
    slugs: set[str] = set()
    for x in active_accounts:
        slug = slug_from_ayrshare_account_label(str(x))
        if slug:
            slugs.add(slug)
    return REQUIRED_LINKED_SOCIAL_PLATFORMS.issubset(slugs)
