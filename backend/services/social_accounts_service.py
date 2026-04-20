"""
Social accounts: DB source of truth, synced from Ayrshare GET /user (server-side only).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session, select

from backend.core.logger import get_logger
from backend.integrations.ayrshare import slug_from_ayrshare_account_label
from backend.models import SocialAccount, User
from backend.services.ayrshare_profile_api import create_user_profile, recover_profile_key_for_user
from backend.services.ayrshare_service import fetch_user_profile_json
from backend.services.team_service import resolve_ayrshare_subject_user

log = get_logger("brokerai.social_accounts")

# Legacy aggregate row from older BrokerAI versions — never expose as a linked account.
_LEGACY_PLATFORM = "ayrshare_profile"


def _slug_platform(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    m = slug_from_ayrshare_account_label(s)
    if m:
        return m
    return s.lower().replace(" ", "").replace("_", "")


def extract_linked_accounts_from_user_payload(
    data: Dict[str, Any], *, profile_key: str
) -> List[Dict[str, Any]]:
    """
    Build one record per linked social account for persistence.
    Each record: platform, account_id, account_name, metadata (dict).
    """
    out: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()
    now_meta = {"source": "ayrshare_user_api"}

    def add_row(platform: str, account_id: str, account_name: str, meta: Dict[str, Any]) -> None:
        plat = _slug_platform(platform)
        if not plat or plat == _LEGACY_PLATFORM:
            return
        aid = (account_id or "").strip() or f"{plat}:default"
        name = (account_name or "").strip() or plat.title()
        key = (plat, aid)
        if key in seen:
            return
        seen.add(key)
        merged = {**now_meta, **meta}
        out.append(
            {
                "platform": plat,
                "account_id": aid,
                "account_name": name,
                "metadata": merged,
            }
        )

    dn = data.get("displayNames")
    if isinstance(dn, list):
        for item in dn:
            if not isinstance(item, dict):
                continue
            plat_raw = item.get("platform") or item.get("network") or item.get("name")
            if not isinstance(plat_raw, str):
                continue
            aid = str(
                item.get("id")
                or item.get("userId")
                or item.get("refId")
                or item.get("profileId")
                or ""
            ).strip()
            un = str(item.get("username") or item.get("userName") or item.get("handle") or "").strip()
            disp = str(item.get("displayName") or item.get("name") or "").strip()
            if not aid:
                aid = f"{_slug_platform(plat_raw)}:{un or disp or 'account'}"
            add_row(
                plat_raw,
                aid,
                disp or un or _slug_platform(plat_raw),
                {"displayNames": True, "profile_key_prefix": profile_key[:8]},
            )

    for plat_key in (
        "facebook",
        "instagram",
        "linkedin",
        "twitter",
        "tiktok",
        "youtube",
        "pinterest",
        "threads",
    ):
        block = data.get(plat_key)
        if not isinstance(block, dict):
            continue
        linked = block.get("linked") is True or block.get("active") is True
        pid = str(block.get("id") or block.get("userId") or "").strip()
        handle = str(
            block.get("username")
            or block.get("userName")
            or block.get("handle")
            or block.get("screenName")
            or ""
        ).strip()
        disp = str(block.get("displayName") or block.get("name") or "").strip()
        if not linked and not (pid or handle or disp):
            continue
        aid = pid or f"{plat_key}:{handle or disp or 'linked'}"
        name = str(block.get("displayName") or block.get("name") or block.get("username") or plat_key).strip()
        add_row(plat_key, aid, name, {"platform_block": plat_key})

    raw = data.get("activeSocialAccounts")
    if isinstance(raw, list) and raw and not out:
        for x in raw:
            if isinstance(x, str):
                p = _slug_platform(x)
                if p:
                    add_row(p, f"{p}:active", p.title(), {"activeSocialAccounts": True})
            elif isinstance(x, dict):
                pr = x.get("platform") or x.get("network")
                if isinstance(pr, str) and pr.strip():
                    add_row(pr, f"{_slug_platform(pr)}:active", pr, {"activeSocialAccounts": True})

    return out


def _public_origin() -> str:
    return (
        os.getenv("BROKERAI_PUBLIC_ORIGIN")
        or os.getenv("PUBLIC_APP_URL")
        or os.getenv("APP_URL")
        or ""
    ).strip().rstrip("/")


def connect_return_url() -> str:
    base = _public_origin()
    if base:
        return f"{base}/connect.html?synced=1"
    return "/connect.html?synced=1"


def ensure_ayrshare_profile_key(session: Session, user: User) -> str:
    """Create or recover Ayrshare Business profile; persist on User."""
    uid = int(user.id or 0)
    pk = (user.ayrshare_profile_key or "").strip()
    if pk:
        return pk
    recovered = recover_profile_key_for_user(uid)
    if recovered:
        user.ayrshare_profile_key = recovered
        session.add(user)
        session.commit()
        session.refresh(user)
        log.info("ensure_ayrshare_profile_key recovered user_id=%s", uid)
        return recovered
    email = (user.email or "").strip()
    new_pk = create_user_profile(uid, email)
    user.ayrshare_profile_key = new_pk
    session.add(user)
    session.commit()
    session.refresh(user)
    log.info("ensure_ayrshare_profile_key created user_id=%s", uid)
    return new_pk


def sync_social_accounts_for_user(session: Session, user_id: int) -> Dict[str, Any]:
    """
    Pull GET /user from Ayrshare and upsert social_accounts rows. Updates User.social_connected.
    """
    u = session.get(User, user_id)
    if u is None:
        return {"ok": False, "error": "user_not_found", "accounts": []}
    subject = resolve_ayrshare_subject_user(session, u)
    uid = int(subject.id or 0)
    profile_key = ensure_ayrshare_profile_key(session, subject)
    if not profile_key:
        return {"ok": False, "error": "no_profile_key", "accounts": []}

    data = fetch_user_profile_json(profile_key, quick=False)
    if data is None:
        log.warning("social_sync_ayrshare_failed user_id=%s", uid)
        return {"ok": False, "error": "ayrshare_unreachable", "accounts": []}

    st = str(data.get("status") or "").strip().lower()
    if st == "error":
        log.warning("social_sync_ayrshare_error_payload user_id=%s", uid)
        now_err = datetime.utcnow()
        existing_err = list(
            session.exec(select(SocialAccount).where(SocialAccount.user_id == uid)).all()
        )
        for row in existing_err:
            if (row.platform or "").strip() == _LEGACY_PLATFORM:
                session.delete(row)
                continue
            if (row.status or "") == "connected":
                row.status = "expired"
                row.is_connected = False
                row.updated_at = now_err
                session.add(row)
        subject.social_connected = False
        session.add(subject)
        session.commit()
        rows_err = list(session.exec(select(SocialAccount).where(SocialAccount.user_id == uid)).all())
        return {
            "ok": False,
            "error": "ayrshare_error",
            "accounts": [_row_to_dict(r) for r in rows_err if (r.platform or "").strip() != _LEGACY_PLATFORM],
            "last_synced_at": None,
        }

    linked = extract_linked_accounts_from_user_payload(data, profile_key=profile_key)
    now = datetime.utcnow()
    linked_keys = {(rec["platform"], rec["account_id"]) for rec in linked}

    existing = list(
        session.exec(select(SocialAccount).where(SocialAccount.user_id == uid)).all()
    )
    by_key: Dict[Tuple[str, str], SocialAccount] = {}
    for row in existing:
        plat = (row.platform or "").strip()
        if plat == _LEGACY_PLATFORM:
            session.delete(row)
            continue
        by_key[(plat, (row.account_id or "").strip())] = row

    for rec in linked:
        key = (rec["platform"], rec["account_id"])
        row = by_key.get(key)
        meta_str = json.dumps(rec.get("metadata") or {}, separators=(",", ":"))[:8000]
        if row is not None and (row.status or "") == "disconnected":
            continue
        if row is not None:
            row.account_name = rec["account_name"][:512]
            row.status = "connected"
            row.is_connected = True
            row.last_synced_at = now
            row.metadata_json = meta_str
            row.profile_key = profile_key
            row.updated_at = now
            session.add(row)
        else:
            session.add(
                SocialAccount(
                    user_id=uid,
                    platform=rec["platform"],
                    profile_key=profile_key,
                    account_id=rec["account_id"],
                    account_name=rec["account_name"][:512],
                    status="connected",
                    last_synced_at=now,
                    metadata_json=meta_str,
                    is_connected=True,
                    created_at=now,
                    updated_at=now,
                )
            )

    for key, row in list(by_key.items()):
        if key in linked_keys:
            continue
        session.delete(row)

    session.commit()
    session.refresh(subject)

    remaining_connected = list(
        session.exec(
            select(SocialAccount).where(
                SocialAccount.user_id == uid,
                SocialAccount.status == "connected",
            )
        ).all()
    )
    subject.social_connected = len(remaining_connected) > 0
    session.add(subject)
    session.commit()
    session.refresh(subject)

    rows = list(session.exec(select(SocialAccount).where(SocialAccount.user_id == uid)).all())
    log.info(
        "social_sync_ok user_id=%s linked_count=%s",
        uid,
        len(linked),
    )
    return {
        "ok": True,
        "error": None,
        "accounts": [_row_to_dict(r) for r in rows],
        "last_synced_at": now.isoformat() + "Z",
    }


def _row_to_dict(row: SocialAccount) -> Dict[str, Any]:
    try:
        meta = json.loads(row.metadata_json or "{}")
    except json.JSONDecodeError:
        meta = {}
    return {
        "id": int(row.id or 0),
        "platform": row.platform,
        "account_id": row.account_id,
        "account_name": row.account_name,
        "status": row.status,
        "last_synced_at": row.last_synced_at.isoformat() + "Z" if row.last_synced_at else None,
        "metadata": meta,
    }


def list_social_accounts(session: Session, user_id: int) -> List[Dict[str, Any]]:
    u = session.get(User, user_id)
    if u is None:
        return []
    subject = resolve_ayrshare_subject_user(session, u)
    uid = int(subject.id or 0)
    rows = list(
        session.exec(
            select(SocialAccount)
            .where(SocialAccount.user_id == uid)
            .where(SocialAccount.platform != _LEGACY_PLATFORM)
        ).all()
    )
    rows.sort(key=lambda r: (r.platform or "", r.account_name or ""))
    return [_row_to_dict(r) for r in rows]


def disconnect_social_account(session: Session, user_id: int, row_id: int) -> bool:
    """Mark a linked row disconnected (Ayrshare unlink is done in their UI; we reflect on next sync)."""
    u = session.get(User, user_id)
    if u is None:
        return False
    subject = resolve_ayrshare_subject_user(session, u)
    uid = int(subject.id or 0)
    row = session.get(SocialAccount, row_id)
    if row is None or int(row.user_id) != uid:
        return False
    row.status = "disconnected"
    row.is_connected = False
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    remaining = list(
        session.exec(
            select(SocialAccount).where(
                SocialAccount.user_id == uid,
                SocialAccount.status == "connected",
            )
        ).all()
    )
    subject.social_connected = len(remaining) > 0
    session.add(subject)
    session.commit()
    return True


def background_sync_all_users() -> int:
    """Sync users that have an Ayrshare profile key. Returns number of users processed."""
    from backend.db import engine

    processed = 0
    with Session(engine) as session:
        users = list(session.exec(select(User)).all())
        for u in users:
            if not (u.ayrshare_profile_key or "").strip():
                continue
            try:
                sync_social_accounts_for_user(session, int(u.id or 0))
                processed += 1
            except Exception:
                log.exception("background_social_sync_failed user_id=%s", u.id)
    return processed
