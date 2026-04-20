"""Email/password signup, login, and password reset (no social login)."""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple, cast

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from backend.auth import hash_password, verify_password
from backend.core.logger import get_logger
from backend.models import PasswordResetToken, User
from backend.services.email_service import send_password_reset_email

log = get_logger("brokerai.auth_service")

_MAX_FAILED = 5
_LOCK_MINUTES = 30


def _utcnow() -> datetime:
    return datetime.utcnow()


def _trial_days() -> int:
    try:
        return max(0, int((os.getenv("BROKERAI_TRIAL_DAYS") or "0").strip()))
    except ValueError:
        return 0


def _public_app_url() -> str:
    return (
        os.getenv("PUBLIC_APP_URL")
        or os.getenv("BROKERAI_PUBLIC_ORIGIN")
        or os.getenv("APP_URL")
        or "http://127.0.0.1:8000"
    ).rstrip("/")


def register_user(
    session: Session,
    *,
    name: str,
    email: str,
    password: str,
    plan_slug: str,
) -> User:
    """Create a new user. Raises ValueError on duplicate email."""
    em = email.strip().lower()
    existing = session.exec(select(User).where(User.email == em)).first()
    if existing is not None:
        raise ValueError("EMAIL_EXISTS")

    trial_days = _trial_days()
    now = _utcnow()
    if trial_days > 0:
        plan_status = "trial"
        plan_expires = now + timedelta(days=trial_days)
    else:
        plan_status = "active"
        plan_expires = None

    u = User(
        email=em,
        password_hash=hash_password(password),
        display_name=name.strip() or None,
        plan=plan_slug,
        plan_status=plan_status,
        plan_expires_at=plan_expires,
        account_type="individual",
        role="owner",
    )
    session.add(u)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ValueError("EMAIL_EXISTS") from None
    session.refresh(u)
    return u


def authenticate_user(session: Session, email: str, password: str) -> Tuple[Optional[User], bool]:
    """Return (user, ok). On wrong password or locked account, ok is False."""
    em = email.strip().lower()
    u = session.exec(select(User).where(User.email == em)).first()
    if u is None:
        return (None, False)

    now = _utcnow()
    locked_until = u.locked_until
    if locked_until is not None and locked_until > now:
        return (u, False)

    if not verify_password(password, u.password_hash):
        u.failed_login_attempts = int(u.failed_login_attempts or 0) + 1
        if u.failed_login_attempts >= _MAX_FAILED:
            u.locked_until = now + timedelta(minutes=_LOCK_MINUTES)
        session.add(u)
        session.commit()
        session.refresh(u)
        return (u, False)

    u.failed_login_attempts = 0
    u.locked_until = None
    u.last_login_at = now
    session.add(u)
    session.commit()
    session.refresh(u)
    return (u, True)


def request_password_reset(session: Session, email: str) -> None:
    """Create reset token and email user (best-effort email). Always swallow errors."""
    em = email.strip().lower()
    u = session.exec(select(User).where(User.email == em)).first()
    if u is None:
        return

    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    now = _utcnow()
    expires = now + timedelta(minutes=15)

    rows = session.exec(select(PasswordResetToken).where(PasswordResetToken.user_id == u.id)).all()
    for row in rows:
        session.delete(cast(PasswordResetToken, row))
    session.add(
        PasswordResetToken(
            user_id=int(u.id),
            token_hash=digest,
            used=False,
            expires_at=expires,
        )
    )
    session.commit()

    # Raw token only in email — never log it.
    link = f"{_public_app_url()}/reset-password.html?token={raw}"
    ok = send_password_reset_email(u.email, link)
    if not ok:
        log.info("password_reset_email_skipped user_id=%s", u.id)


def reset_password_with_token(session: Session, raw_token: str, new_password: str) -> None:
    """Validate one-time token and set new password. Raises ValueError on invalid/expired token."""
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    row = session.exec(select(PasswordResetToken).where(PasswordResetToken.token_hash == digest)).first()
    now = _utcnow()
    if row is None or row.used or row.expires_at < now:
        raise ValueError("INVALID_TOKEN")

    u = session.get(User, row.user_id)
    if u is None:
        raise ValueError("INVALID_TOKEN")

    u.password_hash = hash_password(new_password)
    u.failed_login_attempts = 0
    u.locked_until = None
    session.add(u)
    row.used = True
    session.add(row)
    session.commit()
