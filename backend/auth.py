"""Password hashing, JWT access tokens, and authenticated user resolution."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlmodel import Session, select

from backend.db import get_session
from backend.models import User

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7

_http_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """Hash with bcrypt (for seeds, tests, and auth flows)."""
    pw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        pw = plain.encode("utf-8")[:72]
        h = hashed.encode("ascii")
        return bcrypt.checkpw(pw, h)
    except (ValueError, TypeError):
        return False


def get_user_by_email(session: Session, email: str) -> Optional[User]:
    stmt = select(User).where(User.email == email.strip().lower())
    return session.exec(stmt).first()


def _jwt_secret() -> str:
    key = (os.environ.get("JWT_SECRET_KEY") or "").strip()
    if len(key) < 32:
        raise HTTPException(
            status_code=503,
            detail="Server authentication is not configured (JWT_SECRET_KEY must be at least 32 characters).",
        )
    return key


def create_access_token(user_id: int) -> str:
    now = datetime.utcnow()
    expire = now + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    payload = {"sub": str(int(user_id)), "exp": expire, "iat": now}
    return jwt.encode(payload, _jwt_secret(), algorithm=ALGORITHM)


def decode_access_token_user_id(token: str) -> int:
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if sub is None or not str(sub).isdigit():
            raise JWTError("missing sub")
        return int(sub)
    except JWTError as e:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from e


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_http_bearer),
    session: Session = Depends(get_session),
) -> User:
    """Resolve the current user: pytest header, then Bearer JWT."""
    if os.getenv("PYTEST_CURRENT_TEST"):
        raw = (request.headers.get("x-brokerai-user-id") or "").strip()
        if raw.isdigit():
            u = session.get(User, int(raw))
            if u is not None:
                return u

    token = (credentials.credentials.strip() if credentials and credentials.credentials else "") or ""
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    uid = decode_access_token_user_id(token)
    u = session.get(User, uid)
    if u is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return u
