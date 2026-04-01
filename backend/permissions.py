"""
RBAC permission system for BrokerAI.

Account types:  individual | team | org
Roles:          owner | admin | member

Permission matrix
-----------------
individual/owner  → all actions
team/owner        → all actions
team/member       → create, edit, review (no approve, no publish)
org/owner         → all actions
org/admin         → all actions
org/member        → create, edit, review (no approve, no publish)
"""
from __future__ import annotations

from typing import Callable

from fastapi import Depends, HTTPException

from backend.auth import get_current_user
from backend.models import User

# Actions that are restricted to owner/admin only (in team/org accounts)
_PRIVILEGED_ACTIONS = {"approve_campaign", "publish_campaign"}

# All defined actions
ACTIONS = {
    "create_campaign",
    "edit_campaign",
    "review_campaign",
    "approve_campaign",
    "publish_campaign",
}


def _is_privileged_role(role: str) -> bool:
    return role in ("owner", "admin")


def check_permission(user: User, action: str) -> bool:
    """Return True if the user has the given action permission."""
    account_type: str = (getattr(user, "account_type", None) or "individual").lower()
    role: str = (getattr(user, "role", None) or "owner").lower()

    # Individual accounts always have full access (owner role by definition)
    if account_type == "individual":
        return True

    # team / org accounts
    if action in _PRIVILEGED_ACTIONS:
        return _is_privileged_role(role)

    # Non-privileged actions (create, edit, review) — any role allowed
    return True


def require_permission(action: str) -> Callable:
    """
    FastAPI dependency factory.

    Usage::

        @app.post("/campaigns/{id}/approve")
        async def approve(user: User = Depends(require_permission("approve_campaign"))):
            ...
    """
    if action not in ACTIONS:
        raise ValueError(f"Unknown RBAC action: {action!r}")

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if not check_permission(current_user, action):
            role = getattr(current_user, "role", "member")
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Your role ({role}) does not have permission to "
                    f"{action.replace('_', ' ')}. Only owners and admins can perform this action."
                ),
            )
        return current_user

    return dependency
