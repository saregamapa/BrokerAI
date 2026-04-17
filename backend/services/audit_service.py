"""
S5-08 — Audit log service.
Call audit_log() from any route handler to record a significant event.
Always best-effort — never raises.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlmodel import Session

from backend.core.logger import get_logger
from backend.db import engine
from backend.models import AuditEvent

log = get_logger("brokerai.audit")


# Well-known event type constants
class AuditEventType:
    # Campaigns
    CAMPAIGN_CREATED = "campaign.created"
    CAMPAIGN_UPDATED = "campaign.updated"
    CAMPAIGN_DELETED = "campaign.deleted"
    CAMPAIGN_APPROVED = "campaign.approved"
    CAMPAIGN_GENERATED = "campaign.generated"
    # Posts
    POST_UPDATED = "post.updated"
    POST_PUBLISHED = "post.published"
    POST_FAILED = "post.failed"
    POST_RESCHEDULED = "post.rescheduled"
    POST_APPROVED = "post.approved"
    # Auth
    USER_LOGIN = "user.login"
    USER_SIGNUP = "user.signup"
    USER_PASSWORD_CHANGED = "user.password_changed"
    USER_DELETED = "user.deleted"
    # Team
    TEAM_INVITE_SENT = "team.invite_sent"
    TEAM_MEMBER_REMOVED = "team.member_removed"
    TEAM_ROLE_CHANGED = "team.role_changed"
    # Social
    SOCIAL_CONNECTED = "social.connected"
    SOCIAL_DISCONNECTED = "social.disconnected"


def audit_log(
    event_type: str,
    *,
    actor_user_id: Optional[int] = None,
    entity_type: str = "",
    entity_id: Optional[int] = None,
    summary: str = "",
    diff: Optional[Dict[str, Any]] = None,
    request=None,  # FastAPI Request object (optional, for IP/UA/request_id)
) -> None:
    """
    Record an audit event. Fire-and-forget — never raises.

    Usage:
        audit_log("campaign.created", actor_user_id=user.id, entity_type="campaign",
                  entity_id=campaign.id, summary=f"Created campaign '{campaign.name}'")
    """
    try:
        ip = ""
        ua = ""
        rid = ""
        if request is not None:
            try:
                ip = request.client.host if request.client else ""
                ua = request.headers.get("user-agent", "")[:256]
                rid = getattr(request.state, "request_id", "") or request.headers.get("X-Request-Id", "")
            except Exception:
                pass

        with Session(engine) as session:
            event = AuditEvent(
                actor_user_id=actor_user_id,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                summary=summary[:500],
                diff=json.dumps(diff or {}, default=str),
                ip_address=ip[:64],
                user_agent=ua,
                request_id=rid[:32],
                created_at=datetime.utcnow(),
            )
            session.add(event)
            session.commit()
            log.debug(
                "audit_log event_type=%s actor=%s entity=%s/%s",
                event_type,
                actor_user_id,
                entity_type,
                entity_id,
            )
    except Exception:
        log.exception("audit_log_failed event_type=%s", event_type)


def get_audit_trail(
    session: Session,
    *,
    user_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    limit: int = 50,
) -> list:
    """Retrieve audit events with optional filters."""
    from sqlmodel import select

    stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)
    if user_id is not None:
        stmt = stmt.where(AuditEvent.actor_user_id == user_id)
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    return list(session.exec(stmt).all())
