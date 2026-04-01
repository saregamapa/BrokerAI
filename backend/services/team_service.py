"""
Team management service — invite, list, update role.

Member limits
  team → max_members = 5  (1 owner + 4 members)
  org  → max_members = 13 (1 owner + 3 admins + 9 members → spec says max 13 total)

Admin cap (org only): max 3 admins (excluding owner).
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import HTTPException
from sqlmodel import Session, select

from backend.models import Team, User

# Max admins (non-owner) allowed in an org
_MAX_ORG_ADMINS = 3


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_team_or_404(session: Session, team_id: int) -> Team:
    team = session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


def resolve_ayrshare_subject_user(session: Session, user: User) -> User:
    """
    Ayrshare Business uses one User Profile (Profile-Key) per connected workspace.

    Team/org members (anyone who is not the team's owner user) publish and verify
    social status through the team owner's profile — they do not create their own.
    """
    tid = getattr(user, "team_id", None)
    if tid is None:
        return user
    team = session.get(Team, int(tid))
    if team is None:
        return user
    oid = int(team.owner_id or 0)
    if oid <= 0 or int(user.id or 0) == oid:
        return user
    owner = session.get(User, oid)
    return owner if owner is not None else user


def _require_team_member(user: User, team_id: int) -> None:
    """Raise 403 if the user does not belong to this team."""
    if getattr(user, "team_id", None) != team_id:
        raise HTTPException(status_code=403, detail="You are not a member of this team")


def _require_owner_or_admin(user: User, team_id: int) -> None:
    _require_team_member(user, team_id)
    role = (getattr(user, "role", None) or "member").lower()
    if role not in ("owner", "admin"):
        raise HTTPException(
            status_code=403,
            detail="Only team owners and admins can perform this action",
        )


def _require_owner(user: User, team_id: int) -> None:
    _require_team_member(user, team_id)
    role = (getattr(user, "role", None) or "member").lower()
    if role != "owner":
        raise HTTPException(
            status_code=403,
            detail="Only the team owner can perform this action",
        )


def _count_members(session: Session, team_id: int) -> int:
    return len(
        session.exec(select(User).where(User.team_id == team_id)).all()
    )


def _count_admins(session: Session, team_id: int) -> int:
    """Count non-owner admins."""
    return len(
        session.exec(
            select(User).where(
                User.team_id == team_id,
                User.role == "admin",
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def create_team(
    session: Session,
    *,
    name: str,
    account_type: str,
    owner: User,
) -> Team:
    """Create a new team and assign the owner."""
    max_members = 5 if account_type == "team" else 13
    team = Team(
        name=name,
        account_type=account_type,
        owner_id=int(owner.id or 0),
        max_members=max_members,
        created_at=datetime.utcnow(),
    )
    session.add(team)
    session.commit()
    session.refresh(team)

    # Assign the owner to the team
    owner.team_id = team.id
    owner.role = "owner"
    owner.account_type = account_type
    session.add(owner)
    session.commit()
    session.refresh(owner)
    return team


def invite_member(
    session: Session,
    *,
    team_id: int,
    inviter: User,
    invitee_email: str,
) -> User:
    """
    Invite an existing user by email to join the team.
    The invitee must already have an account.
    Only owners/admins can invite.
    """
    team = _get_team_or_404(session, team_id)
    _require_owner_or_admin(inviter, team_id)

    # Find invitee
    from sqlmodel import select as sel
    invitee = session.exec(sel(User).where(User.email == invitee_email.strip().lower())).first()
    if invitee is None:
        raise HTTPException(
            status_code=404,
            detail=f"No user found with email: {invitee_email}",
        )
    if invitee.team_id == team_id:
        raise HTTPException(status_code=409, detail="User is already a member of this team")
    if invitee.team_id is not None:
        raise HTTPException(status_code=409, detail="User already belongs to another team")

    # Enforce member limit
    current_count = _count_members(session, team_id)
    if current_count >= team.max_members:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Team member limit reached ({team.max_members}). "
                "Upgrade your plan to add more members."
            ),
        )

    invitee.team_id = team_id
    invitee.role = "member"
    invitee.account_type = team.account_type
    session.add(invitee)
    session.commit()
    session.refresh(invitee)
    return invitee


def list_members(session: Session, *, team_id: int, requester: User) -> List[User]:
    """List all members of a team. Requester must be a member."""
    _get_team_or_404(session, team_id)
    _require_team_member(requester, team_id)
    return session.exec(select(User).where(User.team_id == team_id)).all()


def update_member_role(
    session: Session,
    *,
    team_id: int,
    requester: User,
    target_user_id: int,
    new_role: str,
) -> User:
    """
    Update a team member's role.
    - Only the owner can assign the admin role.
    - Owners/admins can demote members to/from member role.
    - Cannot change the owner's own role.
    """
    team = _get_team_or_404(session, team_id)
    _require_owner_or_admin(requester, team_id)

    new_role = new_role.lower()
    if new_role not in ("admin", "member"):
        raise HTTPException(
            status_code=422,
            detail="Role must be 'admin' or 'member' (cannot assign 'owner' via this endpoint)",
        )

    target = session.get(User, target_user_id)
    if target is None or target.team_id != team_id:
        raise HTTPException(status_code=404, detail="Member not found in this team")

    # Cannot change the owner's role
    if target.role == "owner":
        raise HTTPException(status_code=422, detail="Cannot change the team owner's role")

    # Only owner can assign admin role
    if new_role == "admin":
        requester_role = (getattr(requester, "role", None) or "member").lower()
        if requester_role != "owner":
            raise HTTPException(
                status_code=403,
                detail="Only the team owner can assign the admin role",
            )
        # Enforce admin cap for org accounts
        if team.account_type == "org":
            admin_count = _count_admins(session, team_id)
            # Don't count if target is already an admin (role update, not net-new)
            already_admin = target.role == "admin"
            if not already_admin and admin_count >= _MAX_ORG_ADMINS:
                raise HTTPException(
                    status_code=422,
                    detail=f"Organisation admin limit reached ({_MAX_ORG_ADMINS} admins maximum)",
                )

    target.role = new_role
    session.add(target)
    session.commit()
    session.refresh(target)
    return target


def remove_member(
    session: Session,
    *,
    team_id: int,
    requester: User,
    target_user_id: int,
) -> None:
    """Remove a member from the team. Owner/admin only. Cannot remove the owner."""
    _get_team_or_404(session, team_id)
    _require_owner_or_admin(requester, team_id)

    target = session.get(User, target_user_id)
    if target is None or target.team_id != team_id:
        raise HTTPException(status_code=404, detail="Member not found in this team")
    if target.role == "owner":
        raise HTTPException(status_code=422, detail="Cannot remove the team owner")

    target.team_id = None
    target.role = "owner"
    target.account_type = "individual"
    session.add(target)
    session.commit()
