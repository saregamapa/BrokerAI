"""
Strict Post publish workflow transitions (DB source of truth).

Statuses: draft → review → approved → publishing → published | failed; failed → publishing (retry).
"""
from __future__ import annotations

from typing import FrozenSet, Optional, Tuple

from sqlmodel import Session

from backend.core.logger import get_logger
from backend.models import Post

log = get_logger("brokerai.workflow")

POST_DRAFT = "draft"
POST_REVIEW = "review"
POST_APPROVED = "approved"
POST_PUBLISHING = "publishing"
POST_PUBLISHED = "published"
POST_FAILED = "failed"

VALID_STATUSES: FrozenSet[str] = frozenset(
    {
        POST_DRAFT,
        POST_REVIEW,
        POST_APPROVED,
        POST_PUBLISHING,
        POST_PUBLISHED,
        POST_FAILED,
    }
)

# (from_status, to_status)
_ALLOWED: FrozenSet[Tuple[str, str]] = frozenset(
    {
        (POST_DRAFT, POST_REVIEW),
        (POST_REVIEW, POST_APPROVED),
        (POST_APPROVED, POST_PUBLISHING),
        (POST_FAILED, POST_PUBLISHING),
        (POST_PUBLISHING, POST_PUBLISHED),
        (POST_PUBLISHING, POST_FAILED),
    }
)


def is_transition_allowed(current: str, new: str) -> bool:
    if new not in VALID_STATUSES:
        return False
    if current == new:
        return True
    return (current, new) in _ALLOWED


def transition_post_status(
    session: Session,
    row: Post,
    new_status: str,
    *,
    reason: str = "",
    actor: str = "system",
) -> None:
    """Apply a valid transition or raise ValueError."""
    cur = (row.status or "").strip() or POST_DRAFT
    if cur == new_status:
        return
    if not is_transition_allowed(cur, new_status):
        raise ValueError(
            f"Invalid post status transition {cur!r} → {new_status!r} ({reason})"
        )
    log.info(
        "post_status_transition post_id=%s %s → %s actor=%s reason=%s",
        row.id,
        cur,
        new_status,
        actor,
        reason or "-",
    )
    row.status = new_status
    session.add(row)


def normalize_legacy_post_status(raw: Optional[str]) -> str:
    """Map historical status strings to the current model."""
    s = (raw or "").strip()
    mapping = {
        "pending_approval": POST_REVIEW,
        "publish_failed": POST_FAILED,
        "pending": POST_DRAFT,
    }
    return mapping.get(s, s if s in VALID_STATUSES else POST_DRAFT)
