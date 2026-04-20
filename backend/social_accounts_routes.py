"""REST: /api/social-accounts (list, sync, connect, disconnect) and /api/webhooks/ayrshare."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from backend.auth import get_current_user
from backend.core.logger import get_logger
from backend.db import get_session
from backend.models import User
from backend.schemas import (
    SocialAccountOut,
    SocialAccountsListResponse,
    SocialConnectResponse,
    SocialDisconnectRequest,
    SocialDisconnectResponse,
    SocialSyncResponse,
)
from backend.services.ayrshare_profile_api import AyrshareProfileApiError, generate_connect_jwt_url
from backend.services.social_accounts_service import (
    connect_return_url,
    disconnect_social_account,
    ensure_ayrshare_profile_key,
    list_social_accounts,
    sync_social_accounts_for_user,
)
from backend.services.team_service import resolve_ayrshare_subject_user

log = get_logger("brokerai.social_accounts_api")

router = APIRouter(prefix="/api/social-accounts", tags=["social-accounts"])
webhooks_router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _parse_brokerai_user_ref(ref_id: str) -> Optional[int]:
    m = re.match(r"^brokerai_user_(\d+)$", (ref_id or "").strip())
    return int(m.group(1)) if m else None


@router.get("", response_model=SocialAccountsListResponse)
def api_list_social_accounts(
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> SocialAccountsListResponse:
    subject = resolve_ayrshare_subject_user(session, user)
    has_profile = bool((subject.ayrshare_profile_key or "").strip())
    raw = list_social_accounts(session, int(user.id or 0))
    accounts = [SocialAccountOut(**a) for a in raw]
    return SocialAccountsListResponse(accounts=accounts, has_profile=has_profile)


@router.post("/sync", response_model=SocialSyncResponse)
def api_sync_social_accounts(
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> SocialSyncResponse:
    res = sync_social_accounts_for_user(session, int(user.id or 0))
    accs = [SocialAccountOut(**a) for a in res.get("accounts") or []]
    return SocialSyncResponse(
        ok=bool(res.get("ok")),
        error=res.get("error"),
        accounts=accs,
        last_synced_at=res.get("last_synced_at"),
    )


@router.post("/connect", response_model=SocialConnectResponse)
def api_connect_social_accounts(
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> SocialConnectResponse:
    subject = resolve_ayrshare_subject_user(session, user)
    try:
        pk = ensure_ayrshare_profile_key(session, subject)
        url = generate_connect_jwt_url(pk, connect_return_url())
    except AyrshareProfileApiError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    return SocialConnectResponse(url=url)


@router.post("/disconnect", response_model=SocialDisconnectResponse)
def api_disconnect_social_account(
    body: SocialDisconnectRequest,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> SocialDisconnectResponse:
    ok = disconnect_social_account(session, int(user.id or 0), int(body.id))
    if not ok:
        raise HTTPException(status_code=404, detail="Account not found")
    return SocialDisconnectResponse(ok=True)


@webhooks_router.post("/ayrshare")
async def ayrshare_social_webhook(request: Request, session: Session = Depends(get_session)) -> dict[str, Any]:
    """If Ayrshare sends account events, sync the matching BrokerAI user (refId or profileKey)."""
    raw_body = await request.body()
    secret = (
        (os.getenv("AYRSHARE_SOCIAL_WEBHOOK_SECRET") or os.getenv("AYRSHARE_PUBLISH_WEBHOOK_SECRET") or "")
        .strip()
    )
    if secret:
        sig_header = (request.headers.get("x-ayrshare-signature") or "").strip().lower()
        expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig_header):
            log.warning("ayrshare_social_webhook_bad_signature")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        payload: Any = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    if not isinstance(payload, dict):
        payload = {}

    ref = str(payload.get("refId") or payload.get("ref_id") or "").strip()
    pk = str(payload.get("profileKey") or payload.get("profile_key") or "").strip()
    uid: Optional[int] = _parse_brokerai_user_ref(ref) if ref else None
    if uid is None and pk:
        hit = session.exec(select(User).where(User.ayrshare_profile_key == pk)).first()
        if hit is not None:
            uid = int(hit.id or 0)
    if uid is None:
        log.debug("ayrshare_social_webhook_no_user keys=%s", list(payload.keys()))
        return {"accepted": True, "synced": False, "reason": "unknown_user"}

    sync_social_accounts_for_user(session, uid)
    return {"accepted": True, "synced": True, "user_id": uid}
