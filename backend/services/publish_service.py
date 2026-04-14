"""
Idempotent, lock-guarded social publish with retries (DB source of truth).

Scheduler and POST /publish/{id} use safe_publish_post().
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import and_, or_, update
from sqlmodel import Session, select

from backend.core.logger import get_logger
from backend.db import engine
from backend.integrations.ayrshare import (
    coerce_ayrshare_platforms,
    extract_ayrshare_publish_metadata,
    platform_response_json,
    publish_post,
)
from backend.models import Post, User
from backend.services.ayrshare_service import (
    fetch_active_social_accounts,
    linked_social_slugs,
)
from backend.services.team_service import resolve_ayrshare_subject_user
from backend.services.analytics import fetch_post_analytics
from backend.workflow.post_state import (
    POST_APPROVED,
    POST_FAILED,
    POST_PUBLISHED,
    POST_PUBLISHING,
    transition_post_status,
)

log = get_logger("brokerai.publish")

LOCK_TTL_SECONDS = 300


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hashtags_to_list(raw: str) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _truncate_err(payload: Any, limit: int = 2000) -> str:
    s = json.dumps(payload, default=str) if payload is not None else ""
    if len(s) > limit:
        return s[: limit - 3] + "..."
    return s


def compute_publish_idempotency_key(post: Post) -> str:
    """Stable hash for publish idempotency (caption + primary platform + scheduled instant)."""
    plats = coerce_ayrshare_platforms(post.publish_platforms)
    plat = (post.platform or "").strip() or (plats[0] if plats else "")
    sched = ""
    if post.scheduled_at is not None:
        sched = post.scheduled_at.isoformat()
    raw = f"{post.caption or ''}|{plat}|{sched}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:56]


def _backoff_seconds(after_attempt: int) -> int:
    if after_attempt <= 1:
        return 30
    if after_attempt == 2:
        return 120
    return 300


def _lock_expired(lock_ts: Optional[datetime], now: datetime) -> bool:
    if lock_ts is None:
        return True
    return (now - lock_ts).total_seconds() >= LOCK_TTL_SECONDS


@dataclass
class _PublishSnapshot:
    post_id: int
    user_id: int
    caption: str
    hashtags: str
    publish_platforms: Any
    image_url: str
    idempotency_key: str


def recover_stuck_jobs(session: Session) -> int:
    """
    Clear expired locks. If status was publishing, mark failed and schedule retry.
    """
    now = _utc_now_naive()
    stmt = select(Post).where(Post.is_locked == True)  # noqa: E712
    rows = list(session.exec(stmt).all())
    n = 0
    for row in rows:
        if row.id is None or row.lock_timestamp is None:
            continue
        if not _lock_expired(row.lock_timestamp, now):
            continue
        row.is_locked = False
        row.lock_timestamp = None
        if row.status == POST_PUBLISHING:
            row.last_error = (
                ((row.last_error or "").strip() + " | stale_lock_recovered")
                .strip(" |")
            )[:2048]
            try:
                transition_post_status(
                    session,
                    row,
                    POST_FAILED,
                    reason="stale_lock",
                    actor="recover_stuck_jobs",
                )
            except ValueError:
                row.status = POST_FAILED
            row.next_publish_attempt_at = now + timedelta(
                seconds=_backoff_seconds(int(row.publish_attempts or 0))
            )
            log.warning(
                "recovered_stuck_publish post_id=%s (publishing→failed, retry scheduled)",
                row.id,
            )
        session.add(row)
        n += 1
    if n:
        session.commit()
    return n


def _duplicate_published_exists(
    session: Session, idempotency_key: str, exclude_id: int
) -> bool:
    if not idempotency_key:
        return False
    stmt = (
        select(Post.id)
        .where(Post.idempotency_key == idempotency_key)
        .where(Post.status == POST_PUBLISHED)
        .where(Post.id != exclude_id)
    )
    return session.exec(stmt).first() is not None


def try_claim_post_for_publish(
    session: Session,
    post_id: int,
    *,
    now_naive: datetime,
    force_immediate: bool = False,
) -> Optional[_PublishSnapshot]:
    row = session.get(Post, post_id)
    if row is None or row.user_id is None:
        return None

    if row.status == POST_PUBLISHED:
        log.info("publish_skip_already_published post_id=%s", post_id)
        return None

    if row.status == POST_PUBLISHING and row.is_locked and not _lock_expired(
        row.lock_timestamp, now_naive
    ):
        log.debug("publish_skip_in_flight post_id=%s", post_id)
        return None

    max_att = int(getattr(row, "max_attempts", None) or 3)
    if row.publish_attempts >= max_att:
        return None

    if row.status == POST_APPROVED:
        if not force_immediate:
            if row.scheduled_at is None or row.scheduled_at > now_naive:
                return None
    elif row.status == POST_FAILED:
        nxt = row.next_publish_attempt_at
        if nxt is None or nxt > now_naive:
            return None
    else:
        return None

    key = row.idempotency_key or compute_publish_idempotency_key(row)
    if _duplicate_published_exists(session, key, int(row.id)):
        log.warning(
            "publish_duplicate_idempotency_prevented post_id=%s key_prefix=%s",
            post_id,
            key[:12],
        )
        row.last_error = "duplicate_idempotency: another post already published this key"
        row.is_locked = False
        row.lock_timestamp = None
        row.next_publish_attempt_at = None
        try:
            transition_post_status(
                session, row, POST_FAILED, reason="duplicate_key", actor="publish_claim"
            )
        except ValueError:
            row.status = POST_FAILED
        session.add(row)
        session.commit()
        return None

    _pc = Post.__table__.c

    approved_branch = and_(
        Post.status == POST_APPROVED,
        Post.scheduled_at.is_not(None),
        Post.scheduled_at <= now_naive,
    )
    if force_immediate:
        approved_branch = Post.status == POST_APPROVED

    failed_branch = and_(
        Post.status == POST_FAILED,
        Post.next_publish_attempt_at.is_not(None),
        Post.next_publish_attempt_at <= now_naive,
    )

    lock_ok = or_(
        Post.is_locked == False,  # noqa: E712
        Post.is_locked.is_(None),
    )

    stmt = (
        update(Post)
        .where(Post.id == post_id)
        .where(or_(approved_branch, failed_branch))
        .where(_pc.publish_attempts < _pc.max_attempts)
        .where(lock_ok)
        .values(
            status=POST_PUBLISHING,
            is_locked=True,
            lock_timestamp=now_naive,
            publish_attempts=_pc.publish_attempts + 1,
            idempotency_key=key,
            last_error="",
        )
    )

    result = session.execute(stmt)
    session.commit()
    if int(result.rowcount or 0) == 0:
        return None

    session.expire_all()
    row = session.get(Post, post_id)
    if row is None:
        return None
    log.info(
        "publish_claimed post_id=%s attempt=%s force_immediate=%s",
        post_id,
        row.publish_attempts,
        force_immediate,
    )
    return _PublishSnapshot(
        post_id=int(row.id),
        user_id=int(row.user_id),
        caption=row.caption or "",
        hashtags=row.hashtags or "[]",
        publish_platforms=row.publish_platforms,
        image_url=row.image_url or "",
        idempotency_key=key,
    )


def _finalize_publish_result(
    session: Session,
    post_id: int,
    last_result: Dict[str, Any],
    *,
    now_naive: datetime,
) -> None:
    row = session.get(Post, post_id)
    if row is None:
        return

    row.is_locked = False
    row.lock_timestamp = None
    to_store: Dict[str, Any] = dict(last_result or {})
    body_store = to_store.get("body")
    if (
        to_store.get("ok")
        and isinstance(body_store, dict)
        and body_store.get("partial_success")
    ):
        body_copy = dict(body_store)
        body_copy["status"] = "success"
        to_store["body"] = body_copy
    row.platform_response = platform_response_json(to_store)

    if last_result.get("ok"):
        try:
            transition_post_status(
                session, row, POST_PUBLISHED, reason="ayrshare_ok", actor="publish_service"
            )
        except ValueError:
            row.status = POST_PUBLISHED
        row.published_at = now_naive
        row.last_error = ""
        row.next_publish_attempt_at = None
        body = last_result.get("body")
        if isinstance(body, dict):
            sid, plat = extract_ayrshare_publish_metadata(body)
            if sid:
                row.social_post_id = sid
            if plat:
                row.platform = plat
        if not (row.content or "").strip() and (row.caption or "").strip():
            row.content = row.caption
        session.add(row)
        session.commit()
        log.info("publish_success post_id=%s user_id=%s", row.id, row.user_id)
        return

    err_detail = _truncate_err(last_result.get("body"))
    row.last_error = (err_detail or "publish_failed")[:2048]
    max_att = int(getattr(row, "max_attempts", None) or 3)
    attempts = int(row.publish_attempts or 0)

    if attempts >= max_att:
        try:
            transition_post_status(
                session, row, POST_FAILED, reason="max_attempts", actor="publish_service"
            )
        except ValueError:
            row.status = POST_FAILED
        row.next_publish_attempt_at = None
        log.warning(
            "publish_terminal_failure post_id=%s attempts=%s body=%s",
            row.id,
            attempts,
            last_result.get("body"),
        )
    else:
        delay = _backoff_seconds(attempts)
        row.next_publish_attempt_at = now_naive + timedelta(seconds=delay)
        try:
            transition_post_status(
                session, row, POST_FAILED, reason="retry_scheduled", actor="publish_service"
            )
        except ValueError:
            row.status = POST_FAILED
        log.info(
            "publish_retry_scheduled post_id=%s attempt=%s next_in_s=%s",
            row.id,
            attempts,
            delay,
        )
    session.add(row)
    session.commit()


async def safe_publish_post(post_id: int, *, force_immediate: bool = False) -> Dict[str, Any]:
    now_naive = _utc_now_naive()

    with Session(engine) as session:
        row = session.get(Post, post_id)
        if row is None:
            return {"ok": False, "error": "not_found"}
        if row.status == POST_PUBLISHED:
            log.info("publish_idempotent_noop post_id=%s (already published)", post_id)
            return {"ok": True, "no_op": True, "status": POST_PUBLISHED}

    with Session(engine) as session:
        snap = try_claim_post_for_publish(
            session,
            post_id,
            now_naive=now_naive,
            force_immediate=force_immediate,
        )

    if snap is None:
        with Session(engine) as s2:
            r2 = s2.get(Post, post_id)
            st = r2.status if r2 else None
        return {"ok": False, "error": "not_eligible", "status": st}

    tags = _hashtags_to_list(snap.hashtags)
    tail = " ".join(tags)
    full_caption = snap.caption if not tail else f"{snap.caption}\n\n{tail}"
    pl = coerce_ayrshare_platforms(snap.publish_platforms)
    image_url = (snap.image_url or "").strip()
    media_urls = (
        [image_url]
        if image_url.lower().startswith(("http://", "https://"))
        else None
    )

    with Session(engine) as session:
        post_user = session.get(User, snap.user_id)
        subject = (
            resolve_ayrshare_subject_user(session, post_user)
            if post_user is not None
            else None
        )
        profile_key = (
            (subject.ayrshare_profile_key or "").strip() if subject is not None else ""
        )

    single_primary = os.getenv("AYRSHARE_SINGLE_ACCOUNT_PUBLISH", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not single_primary and profile_key:
        active = fetch_active_social_accounts(profile_key)
        if active is not None:
            linked = linked_social_slugs(active)
            if linked:
                before = list(pl)
                pl = [p for p in pl if p in linked]
                if len(pl) < len(before):
                    log.info(
                        "publish_platforms_filtered post_id=%s before=%s after=%s linked=%s",
                        post_id,
                        before,
                        pl,
                        sorted(linked),
                    )
            else:
                pl = []
            if not pl:
                last_result = {
                    "ok": False,
                    "status_code": 0,
                    "body": {
                        "error": "not_connected",
                        "detail": "No Ayrshare-linked networks match this post's platforms.",
                        "user_message": (
                            "Connect the networks you want on Connect Accounts, "
                            "or edit the post to use only linked platforms."
                        ),
                        "retryable": False,
                        "action": "reconnect",
                    },
                }
                now_naive = _utc_now_naive()
                with Session(engine) as session:
                    _finalize_publish_result(session, post_id, last_result, now_naive=now_naive)
                with Session(engine) as session:
                    row = session.get(Post, post_id)
                    return {
                        "ok": False,
                        "status": row.status if row else None,
                    }

    try:
        last_result = await publish_post(
            full_caption,
            pl,
            media_urls=media_urls,
            profile_key=profile_key or None,
        )
    except Exception as e:
        log.exception("publish_post_exception post_id=%s", post_id)
        last_result = {
            "ok": False,
            "status_code": 0,
            "body": {"error": "exception", "detail": str(e)},
        }

    now_naive = _utc_now_naive()
    with Session(engine) as session:
        _finalize_publish_result(session, post_id, last_result, now_naive=now_naive)

    with Session(engine) as session:
        row = session.get(Post, post_id)
        out: Dict[str, Any] = {
            "ok": bool(last_result.get("ok")),
            "status": row.status if row else None,
        }
        if last_result.get("ok") and row and row.user_id:
            try:
                await fetch_post_analytics(session, int(row.id), int(row.user_id))
            except Exception:
                log.warning(
                    "post analytics after publish failed post_id=%s", post_id, exc_info=True
                )
        return out


async def publish_due_posts_workflow() -> None:
    now_naive = _utc_now_naive()
    with Session(engine) as session:
        recover_stuck_jobs(session)

    pc = Post.__table__.c
    with Session(engine) as session:
        stmt_appr = (
            select(Post.id)
            .where(Post.status == POST_APPROVED)
            .where(Post.scheduled_at.is_not(None))
            .where(Post.scheduled_at <= now_naive)
            .where(pc.publish_attempts < pc.max_attempts)
            .where(
                or_(
                    Post.is_locked == False,  # noqa: E712
                    Post.is_locked.is_(None),
                )
            )
        )
        stmt_fail = (
            select(Post.id)
            .where(Post.status == POST_FAILED)
            .where(Post.next_publish_attempt_at.is_not(None))
            .where(Post.next_publish_attempt_at <= now_naive)
            .where(pc.publish_attempts < pc.max_attempts)
            .where(
                or_(
                    Post.is_locked == False,  # noqa: E712
                    Post.is_locked.is_(None),
                )
            )
        )
        ids = sorted(
            set(session.exec(stmt_appr).all()) | set(session.exec(stmt_fail).all())
        )

    for pid in ids:
        if pid is None:
            continue
        await safe_publish_post(int(pid), force_immediate=False)
