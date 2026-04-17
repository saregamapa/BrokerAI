"""Authentication utilities for BrokerAI.

Sprint 0 hardening:
  - S0-01: JWT_SECRET_KEY is REQUIRED. Server refuses to start if missing/placeholder.
  - S0-03: Short-lived access tokens (15 min) + long-lived refresh tokens (30 days).
  - S0-04: Password reset token helpers (generate, hash, verify).
  - S0-06: Email verification token helpers.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Cookie, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlmodel import Session, select

from backend.db import get_session
from backend.models import RefreshToken, User

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALGORITHM = "HS256"

# Sprint 0 S0-03: short-lived access tokens, long-lived refresh tokens
ACCESS_TOKEN_EXPIRE_MINUTES = 15          # Was 7 days — now 15 minutes
REFRESH_TOKEN_EXPIRE_DAYS = 30

# One-time token TTLs (password reset, email verification)
PASSWORD_RESET_TOKEN_EXPIRE_MINUTES = 15
EMAIL_VERIFY_TOKEN_EXPIRE_HOURS = 24

security = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# S0-01: JWT Secret — hard crash if missing or placeholder
# ---------------------------------------------------------------------------

_KNOWN_PLACEHOLDERS = {
    "brokerai-dev-change-me-in-production",
    "brokerai-dev",
    "changeme",
    "change-me",
    "dev-secret",
    "secret",
    "dev",
    "test",
    "testing",
    "replace-me",
    "placeholder",
}


def _secret_key() -> str:
    """Return JWT_SECRET_KEY. Raises RuntimeError if missing or placeholder."""
    key = os.getenv("JWT_SECRET_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "[BrokerAI] FATAL: JWT_SECRET_KEY is not set. "
            "Generate a strong secret: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    if key.lower() in _KNOWN_PLACEHOLDERS or len(key) < 16:
        raise RuntimeError(
            "[BrokerAI] FATAL: JWT_SECRET_KEY is a known placeholder or too short (< 16 chars). "
            "Set a strong random value before starting the server."
        )
    return key


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Hash with bcrypt (no passlib — avoids bcrypt 4.x / passlib incompatibility)."""
    pw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        pw = plain.encode("utf-8")[:72]
        h = hashed.encode("ascii")
        return bcrypt.checkpw(pw, h)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# S0-03: Access token (15 min, JWT)
# ---------------------------------------------------------------------------

def create_access_token(user_id: int) -> str:
    """Create a short-lived JWT access token (15 minutes)."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "sub": str(user_id),
        "exp": expire,
        "iat": now,
        "type": "access",
    }
    raw = jwt.encode(to_encode, _secret_key(), algorithm=ALGORITHM)
    return raw if isinstance(raw, str) else raw.decode("ascii")


def decode_token(token: str) -> int:
    """Decode and validate a JWT access token. Returns user_id."""
    try:
        payload = jwt.decode(token, _secret_key(), algorithms=[ALGORITHM])
        sub = payload.get("sub")
        token_type = payload.get("type", "access")
        if sub is None:
            raise HTTPException(status_code=401, detail="Invalid token: no subject")
        if token_type != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return int(sub)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc


# ---------------------------------------------------------------------------
# S0-03: Refresh token (30 days, opaque, DB-backed)
# ---------------------------------------------------------------------------

def _hash_token(raw: str) -> str:
    """SHA-256 hash of a raw token string — safe to store in DB."""
    return hashlib.sha256(raw.encode()).hexdigest()


def create_refresh_token(user_id: int, session: Session) -> str:
    """Generate a 48-byte random refresh token, persist hash in DB, return raw value."""
    raw = secrets.token_urlsafe(48)
    token_hash = _hash_token(raw)
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    db_token = RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at.replace(tzinfo=None),  # store as UTC naive
    )
    session.add(db_token)
    session.commit()
    return raw


def verify_refresh_token(raw: str, session: Session) -> Optional[RefreshToken]:
    """Look up a refresh token by hash. Returns the DB record if valid, else None."""
    token_hash = _hash_token(raw)
    stmt = select(RefreshToken).where(
        RefreshToken.token_hash == token_hash,
        RefreshToken.revoked == False,  # noqa: E712
    )
    db_token = session.exec(stmt).first()
    if db_token is None:
        return None
    # Check expiry
    now = datetime.utcnow()
    if db_token.expires_at < now:
        # Expired — revoke it
        db_token.revoked = True
        session.add(db_token)
        session.commit()
        return None
    return db_token


def revoke_refresh_token(raw: str, session: Session) -> None:
    """Revoke a refresh token (logout). No-op if not found."""
    token_hash = _hash_token(raw)
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    db_token = session.exec(stmt).first()
    if db_token:
        db_token.revoked = True
        session.add(db_token)
        session.commit()


def revoke_all_user_tokens(user_id: int, session: Session) -> None:
    """Revoke ALL refresh tokens for a user (e.g., after password change)."""
    stmt = select(RefreshToken).where(
        RefreshToken.user_id == user_id,
        RefreshToken.revoked == False,  # noqa: E712
    )
    tokens = session.exec(stmt).all()
    for token in tokens:
        token.revoked = True
        session.add(token)
    session.commit()


def set_refresh_cookie(response: Response, raw_token: str) -> None:
    """Set refresh token as an HttpOnly, SameSite=Lax cookie."""
    response.set_cookie(
        key="refresh_token",
        value=raw_token,
        httponly=True,
        secure=os.getenv("BROKERAI_ENV", "").lower() in ("prod", "production", "staging"),
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/auth/refresh",
    )


def clear_refresh_cookie(response: Response) -> None:
    """Clear the refresh_token cookie on logout."""
    response.delete_cookie(key="refresh_token", path="/auth/refresh")


# ---------------------------------------------------------------------------
# S0-04: Password reset token helpers
# ---------------------------------------------------------------------------

def generate_reset_token() -> tuple[str, str]:
    """Generate (raw_token, hashed_token) pair for password reset."""
    raw = secrets.token_urlsafe(32)
    hashed = _hash_token(raw)
    return raw, hashed


def hash_reset_token(raw: str) -> str:
    return _hash_token(raw)


# ---------------------------------------------------------------------------
# S0-06: Email verification token helpers
# ---------------------------------------------------------------------------

def generate_email_verify_token() -> tuple[str, str]:
    """Generate (raw_token, hashed_token) pair for email verification."""
    raw = secrets.token_urlsafe(32)
    hashed = _hash_token(raw)
    return raw, hashed


# ---------------------------------------------------------------------------
# FastAPI dependency: get current user from Bearer token
# ---------------------------------------------------------------------------

def get_current_user(
    session: Session = Depends(get_session),
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> User:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = decode_token(creds.credentials)
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def get_user_by_email(session: Session, email: str) -> Optional[User]:
    stmt = select(User).where(User.email == email.strip().lower())
    return session.exec(stmt).first()
