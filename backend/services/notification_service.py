"""
S3-01 — Notification service.

Called by publish_service, webhook handler, and approval flows to
create in-app Notification rows. The frontend polls GET /notifications.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from backend.core.logger import get_logger
from backend.db import engine
from backend.models import Notification

log = get_logger("brokerai.notifications")

# Maximum notifications to return in the list endpoint
MAX_NOTIFICATIONS = 50


def create_notification(
    user_id: int,
    type: str,
    title: str,
    message: str,
    *,
    action_url: str = "",
    extra: Optional[Dict[str, Any]] = None,
    # backwards-compat alias (old callers may pass metadata=)
    metadata: Optional[Dict[str, Any]] = None,
) -> Notification:
    """Create and persist a notification. Safe to call from any context."""
    payload = extra or metadata or {}
    with Session(engine) as session:
        notif = Notification(
            user_id=user_id,
            type=type,
            title=title,
            message=message,
            action_url=action_url,
            is_read=False,
            extra=json.dumps(payload),
        )
        session.add(notif)
        session.commit()
        session.refresh(notif)
        log.debug("notification_created user_id=%s type=%s id=%s", user_id, type, notif.id)
        return notif


def get_notifications(session: Session, user_id: int) -> List[Notification]:
    stmt = (
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(MAX_NOTIFICATIONS)
    )
    return list(session.exec(stmt).all())


def get_unread_count(session: Session, user_id: int) -> int:
    stmt = (
        select(Notification)
        .where(Notification.user_id == user_id)
        .where(Notification.is_read == False)  # noqa: E712
    )
    return len(list(session.exec(stmt).all()))


def mark_notification_read(session: Session, notification_id: int, user_id: int) -> bool:
    notif = session.get(Notification, notification_id)
    if notif is None or notif.user_id != user_id:
        return False
    notif.is_read = True
    session.add(notif)
    session.commit()
    return True


def mark_all_read(session: Session, user_id: int) -> int:
    stmt = (
        select(Notification)
        .where(Notification.user_id == user_id)
        .where(Notification.is_read == False)  # noqa: E712
    )
    rows = list(session.exec(stmt).all())
    for n in rows:
        n.is_read = True
        session.add(n)
    session.commit()
    return len(rows)
