"""Ayrshare Business Plan: create user profiles and JWT SSO connect URLs (server-side only)."""
from __future__ import annotations

import base64
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx

from backend.core.logger import get_logger
from backend.integrations.ayrshare import normalize_ayrshare_api_key

log = get_logger("brokerai.ayrshare_profile_api")

AYRSHARE_API_CREATE_PROFILE = "https://api.ayrshare.com/api/profiles"
AYRSHARE_API_GENERATE_JWT = "https://api.ayrshare.com/api/profiles/generateJWT"
AYRSHARE_API_GET_PROFILES = "https://api.ayrshare.com/api/profiles"


class AyrshareProfileApiError(Exception):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _api_key() -> str:
    return normalize_ayrshare_api_key(os.getenv("AYRSHARE_API_KEY", ""))


def brokerai_profile_ref_id(user_id: int) -> str:
    return f"brokerai_user_{int(user_id)}"


def brokerai_profile_title(user_id: int) -> str:
    return f"BrokerAI User {int(user_id)}"


def _normalize_private_key_pem(pem: str) -> str:
    s = pem.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not s:
        return ""
    s = s.lstrip("\ufeff")
    return s + ("\n" if not s.endswith("\n") else "")


def _load_private_key_for_jwt() -> Tuple[str, bool]:
    b64_flag = os.getenv("AYRSHARE_PRIVATE_KEY_BASE64", "").strip().lower() in ("1", "true", "yes")
    b64_val = os.getenv("AYRSHARE_PRIVATE_KEY_BASE64_VALUE", "").strip()
    if b64_val:
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


def fetch_profiles_by_ref_id(
    ref_id: str, *, include: Optional[str] = None, timeout_sec: float = 20.0
) -> Optional[List[Dict[str, Any]]]:
    key = _api_key()
    rid = str(ref_id or "").strip()
    if not key or not rid:
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
        log.warning("Ayrshare GET /profiles ref_id=%s err=%s", rid, e)
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    if resp.status_code >= 400 or not isinstance(data, dict) or data.get("status") == "error":
        return None
    profiles = data.get("profiles")
    if not isinstance(profiles, list):
        return []
    return [p for p in profiles if isinstance(p, dict)]


def fetch_all_business_profiles(*, timeout_sec: float = 25.0) -> Optional[List[Dict[str, Any]]]:
    key = _api_key()
    if not key:
        return None
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            resp = client.get(
                AYRSHARE_API_GET_PROFILES,
                headers={"Authorization": f"Bearer {key}"},
            )
    except Exception as e:
        log.warning("Ayrshare GET /profiles (all) err=%s", e)
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    if resp.status_code >= 400 or not isinstance(data, dict) or data.get("status") == "error":
        return None
    profiles = data.get("profiles")
    if not isinstance(profiles, list):
        return []
    return [p for p in profiles if isinstance(p, dict)]


def recover_profile_key_for_user(user_id: int, *, timeout_sec: float = 25.0) -> Optional[str]:
    ref = brokerai_profile_ref_id(user_id)
    title = brokerai_profile_title(user_id)
    title_lower = title.lower()
    rows = fetch_profiles_by_ref_id(ref, timeout_sec=timeout_sec) or []
    for p in rows:
        pk = str(p.get("profileKey") or "").strip()
        if pk:
            return pk
    all_rows = fetch_all_business_profiles(timeout_sec=timeout_sec) or []
    for p in all_rows:
        rid = str(p.get("refId") or "").strip()
        t = str(p.get("title") or "").strip()
        # Match on refId (exact) or exact title only — avoid prefix matches that could
        # attach the wrong profileKey if another profile's title starts the same way.
        if rid == ref or t.lower() == title_lower:
            pk = str(p.get("profileKey") or "").strip()
            if pk:
                return pk
    return None


def create_user_profile(user_id: int, _email: str) -> str:
    key = _api_key()
    if not key:
        raise AyrshareProfileApiError("AYRSHARE_API_KEY is not configured", status_code=503)
    payload = {
        "title": brokerai_profile_title(user_id),
        "refId": brokerai_profile_ref_id(user_id),
    }
    try:
        with httpx.Client(timeout=25.0) as client:
            resp = client.post(
                AYRSHARE_API_CREATE_PROFILE,
                json=payload,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
    except httpx.RequestError as e:
        raise AyrshareProfileApiError(f"Ayrshare profile creation failed: {e}", status_code=502) from e
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    if resp.status_code >= 400 or not isinstance(data, dict):
        msg = str(data.get("message") if isinstance(data, dict) else "Invalid response")
        if "Profile title already exists" in msg or "duplicate" in msg.lower():
            recovered = recover_profile_key_for_user(user_id)
            if recovered:
                log.info("create_user_profile duplicate title recovered (4xx path) user_id=%s", user_id)
                return recovered
            unique_suffix = uuid.uuid4().hex[:8]
            fallback_title = f"BrokerAI User {int(user_id)} {unique_suffix}"
            log.warning(
                "create_user_profile 4xx recovery failed, unique title fallback user_id=%s title=%s",
                user_id, fallback_title,
            )
            fallback_payload = {
                "title": fallback_title,
                "refId": brokerai_profile_ref_id(user_id),
            }
            try:
                with httpx.Client(timeout=25.0) as client:
                    fb_resp = client.post(
                        AYRSHARE_API_CREATE_PROFILE,
                        json=fallback_payload,
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    )
                fb_data = fb_resp.json() if fb_resp.content else {}
                if fb_resp.status_code < 400 and isinstance(fb_data, dict) and fb_data.get("status") != "error":
                    fb_pk = str(fb_data.get("profileKey") or "").strip()
                    if fb_pk:
                        log.info("create_user_profile 4xx unique-title fallback succeeded user_id=%s", user_id)
                        return fb_pk
            except Exception as fb_exc:
                log.warning("create_user_profile 4xx unique-title fallback failed: %s", fb_exc)
            raise AyrshareProfileApiError(
                "Could not create your social profile on Ayrshare. "
                "A profile with this name already exists and could not be recovered. "
                "Please contact support or try reconnecting from Settings.",
                status_code=502,
            )
        raise AyrshareProfileApiError(msg or "Could not create Ayrshare user profile", status_code=502)
    if data.get("status") == "error":
        msg = str(data.get("message") or "Ayrshare profile creation rejected")
        if "Profile title already exists" in msg or "duplicate" in msg.lower():
            # First: try to recover the existing profile (same user, DB was reset)
            recovered = recover_profile_key_for_user(user_id)
            if recovered:
                log.info("create_user_profile duplicate title recovered user_id=%s", user_id)
                return recovered
            # Second: recovery failed — create a new profile with a unique title suffix
            # so the user is never permanently blocked by a stale Ayrshare profile.
            unique_suffix = uuid.uuid4().hex[:8]
            fallback_title = f"BrokerAI User {int(user_id)} {unique_suffix}"
            log.warning(
                "create_user_profile recovery failed, creating with unique title user_id=%s title=%s",
                user_id, fallback_title,
            )
            fallback_payload = {
                "title": fallback_title,
                "refId": brokerai_profile_ref_id(user_id),
            }
            try:
                with httpx.Client(timeout=25.0) as client:
                    fb_resp = client.post(
                        AYRSHARE_API_CREATE_PROFILE,
                        json=fallback_payload,
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    )
                fb_data = fb_resp.json() if fb_resp.content else {}
                if fb_resp.status_code < 400 and isinstance(fb_data, dict) and fb_data.get("status") != "error":
                    fb_pk = str(fb_data.get("profileKey") or "").strip()
                    if fb_pk:
                        log.info("create_user_profile unique-title fallback succeeded user_id=%s", user_id)
                        return fb_pk
            except Exception as fb_exc:
                log.warning("create_user_profile unique-title fallback also failed: %s", fb_exc)
            raise AyrshareProfileApiError(
                "Could not create your social profile on Ayrshare. "
                "A profile with this name already exists and could not be recovered. "
                "Please contact support or try reconnecting from Settings.",
                status_code=502,
            )
        raise AyrshareProfileApiError(msg, status_code=502)
    pk = data.get("profileKey")
    if not isinstance(pk, str) or not pk.strip():
        raise AyrshareProfileApiError("Ayrshare did not return a profile key", status_code=502)
    return pk.strip()


def generate_connect_jwt_url(
    profile_key: str, redirect_after_connect: Optional[str] = None
) -> str:
    key = _api_key()
    domain = _sso_domain()
    private_key, pk_is_b64 = _load_private_key_for_jwt()
    if not key:
        raise AyrshareProfileApiError("AYRSHARE_API_KEY is not configured", status_code=503)
    if not domain or not private_key:
        raise AyrshareProfileApiError(
            "Social SSO is not configured. Set AYRSHARE_SSO_DOMAIN and AYRSHARE_PRIVATE_KEY "
            "(or AYRSHARE_PRIVATE_KEY_PATH).",
            status_code=503,
        )
    pk = profile_key.strip()
    body: Dict[str, Any] = {
        "domain": domain,
        "privateKey": private_key,
        "profileKey": pk,
        "allowedSocial": ["facebook", "instagram", "linkedin", "twitter", "tiktok", "youtube"],
    }
    if pk_is_b64:
        body["privateKeyBase64"] = True
    if redirect_after_connect and redirect_after_connect.strip():
        body["redirect"] = redirect_after_connect.strip()
    try:
        with httpx.Client(timeout=25.0) as client:
            resp = client.post(
                AYRSHARE_API_GENERATE_JWT,
                json=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
    except httpx.RequestError as e:
        raise AyrshareProfileApiError(f"Ayrshare JWT generation failed: {e}", status_code=502) from e
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    if not isinstance(data, dict) or resp.status_code >= 400 or data.get("status") == "error":
        if isinstance(data, dict) and data.get("status") == "error" and resp.status_code < 500:
            mode = os.getenv("AYRSHARE_GENERATE_JWT_MODE", "json").strip().lower()
            if mode == "json" and not pk_is_b64:
                form: List[Tuple[str, str]] = [
                    ("domain", domain),
                    ("privateKey", private_key),
                    ("profileKey", pk),
                ]
                if redirect_after_connect and redirect_after_connect.strip():
                    form.append(("redirect", redirect_after_connect.strip()))
                form_encoded = urlencode(form)
                with httpx.Client(timeout=25.0) as client:
                    resp = client.post(
                        AYRSHARE_API_GENERATE_JWT,
                        content=form_encoded,
                        headers={
                            "Authorization": f"Bearer {key}",
                            "Content-Type": "application/x-www-form-urlencoded",
                        },
                    )
                try:
                    data = resp.json()
                except Exception:
                    data = {}
        if not isinstance(data, dict) or resp.status_code >= 400 or data.get("status") == "error":
            raise AyrshareProfileApiError(
                str(data.get("message") if isinstance(data, dict) else "JWT generation failed"),
                status_code=502,
            )
    url = data.get("url")
    if not isinstance(url, str) or not url.strip():
        raise AyrshareProfileApiError("Ayrshare did not return a connect URL", status_code=502)
    return url.strip()
