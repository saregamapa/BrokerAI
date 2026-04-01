"""
Invite service — token-based team member signup flow.

Flow:
  1. Owner calls POST /teams/{team_id}/invite  → creates TeamInvite row, returns link
  2. Invitee visits /signup?invite_token=XYZ
  3. Invitee submits form → POST /signup with invite_token in body
  4. Signup validates token, creates user attached to team

Constraints:
  - Only team owner can create invites
  - Invite email must match signup email exactly (case-insensitive)
  - Token expires after INVITE_TTL_HOURS (default 72 h)
  - Token is single-use (is_used = True after redemption)
  - Duplicate invite emails within a team are allowed (old token remains but only
    one can be used since the user would already be in the team on second attempt)
  - Member limit enforced at invite creation AND at signup
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlmodel import Session, select

from backend.models import Team, TeamInvite, User

INVITE_TTL_HOURS = 72
_TOKEN_BYTES = 32  # 256-bit → url-safe base64 ≈ 43 chars


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.utcnow()


def _get_team_or_404(session: Session, team_id: int) -> Team:
    team = session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


def _require_owner(user: User, team_id: int) -> None:
    if getattr(user, "team_id", None) != team_id:
        raise HTTPException(status_code=403, detail="You are not a member of this team")
    if (getattr(user, "role", None) or "member").lower() != "owner":
        raise HTTPException(status_code=403, detail="Only the team owner can send invites")


def _count_members(session: Session, team_id: int) -> int:
    return len(session.exec(select(User).where(User.team_id == team_id)).all())


def _count_pending_invites(session: Session, team_id: int) -> int:
    """
    Count pending (unused, not-expired) invites for users who don't yet exist,
    to avoid over-issuing invites beyond the team limit.
    """
    now = _utcnow()
    rows = session.exec(
        select(TeamInvite).where(
            TeamInvite.team_id == team_id,
            TeamInvite.is_used == False,  # noqa: E712
            TeamInvite.expires_at > now,
        )
    ).all()
    # Only count invites where the invitee hasn't joined yet
    pending = 0
    for row in rows:
        existing = session.exec(
            select(User).where(
                User.email == row.email.strip().lower(),
                User.team_id == team_id,
            )
        ).first()
        if existing is None:
            pending += 1
    return pending


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_invite(
    session: Session,
    *,
    team_id: int,
    requester: User,
    email: str,
    base_url: str = "http://localhost:8000",
) -> TeamInvite:
    """
    Create a new invite for *email* to join *team_id*.
    Returns the saved TeamInvite row (contains the signup URL in .signup_url property,
    but callers compute the URL themselves from token).

    Raises HTTPException on validation failures.
    """
    email = email.strip().lower()
    team = _get_team_or_404(session, team_id)
    _require_owner(requester, team_id)

    # Check invitee isn't already a member of this team
    already_member = session.exec(
        select(User).where(
            User.email == email,
            User.team_id == team_id,
        )
    ).first()
    if already_member is not None:
        raise HTTPException(
            status_code=409,
            detail=f"{email} is already a member of this team",
        )

    # Enforce member limit: current members + pending invites < max_members
    current = _count_members(session, team_id)
    pending = _count_pending_invites(session, team_id)
    if current + pending >= team.max_members:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Team member limit reached ({team.max_members} users). "
                "Remove existing invites or members before sending new ones."
            ),
        )

    # Invalidate any prior unused invite for this email+team combo
    # (makes UX cleaner — only latest link works)
    old_invites = session.exec(
        select(TeamInvite).where(
            TeamInvite.team_id == team_id,
            TeamInvite.email == email,
            TeamInvite.is_used == False,  # noqa: E712
        )
    ).all()
    for old in old_invites:
        # Expire immediately so old links fail gracefully
        old.expires_at = _utcnow() - timedelta(seconds=1)
        session.add(old)

    token = secrets.token_urlsafe(_TOKEN_BYTES)
    invite = TeamInvite(
        email=email,
        team_id=team_id,
        role="member",
        token=token,
        is_used=False,
        expires_at=_utcnow() + timedelta(hours=INVITE_TTL_HOURS),
        created_at=_utcnow(),
    )
    session.add(invite)
    session.commit()
    session.refresh(invite)
    return invite


def validate_and_redeem_invite(
    session: Session,
    *,
    token: str,
    signup_email: str,
) -> TeamInvite:
    """
    Validate an invite token and mark it as used.

    Must be called inside the same DB transaction as user creation
    (caller commits after creating the user + calling this).

    Returns the TeamInvite row on success.
    Raises HTTPException with a precise error code on any failure.
    """
    signup_email = signup_email.strip().lower()

    invite = session.exec(
        select(TeamInvite).where(TeamInvite.token == token)
    ).first()

    if invite is None:
        raise HTTPException(status_code=400, detail="Invalid invite token")

    if invite.is_used:
        raise HTTPException(status_code=400, detail="This invite link has already been used")

    if _utcnow() > invite.expires_at:
        raise HTTPException(
            status_code=400,
            detail="This invite link has expired. Ask the team owner to send a new one.",
        )

    if invite.email.strip().lower() != signup_email:
        raise HTTPException(
            status_code=400,
            detail="This invite was sent to a different email address. Sign up with the invited email.",
        )

    # Check team still exists and has capacity
    team = session.get(Team, invite.team_id)
    if team is None:
        raise HTTPException(status_code=400, detail="The team no longer exists")

    current = _count_members(session, invite.team_id)
    if current >= team.max_members:
        raise HTTPException(
            status_code=422,
            detail="Team is full. Ask the owner to remove a member first.",
        )

    # Mark as used (caller must commit)
    invite.is_used = True
    session.add(invite)
    return invite


def get_invite_by_token(session: Session, token: str) -> Optional[TeamInvite]:
    """Read-only lookup — used by the frontend pre-fill endpoint."""
    return session.exec(
        select(TeamInvite).where(TeamInvite.token == token)
    ).first()
