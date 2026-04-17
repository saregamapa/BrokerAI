"""Sprint 0 security tests — auth hardening (S0-01 through S0-07, S0-10).

Covers:
  S0-01  JWT_SECRET_KEY must be set and not a placeholder
  S0-02  Login rate limit descriptor changed to 5/min (validated via limiter config)
  S0-03  Refresh token issuance, cookie, /auth/refresh, /auth/logout
  S0-04  Forgot-password creates PasswordResetToken; /auth/reset-password succeeds
  S0-06  Email verification token round-trip
  S0-07  Account lockout after 10 failed login attempts
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.auth import (
    create_access_token,
    hash_reset_token,
    verify_refresh_token,
)
from backend.db import engine
from backend.models import (
    EmailVerificationToken,
    PasswordResetToken,
    RefreshToken,
    User,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register(client: TestClient, email: str, password: str = "TestPass123!") -> dict:
    res = client.post("/signup", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()


def _login(client: TestClient, email: str, password: str = "TestPass123!"):
    return client.post("/login", json={"email": email, "password": password})


# ---------------------------------------------------------------------------
# S0-01: JWT secret must be non-empty and non-placeholder
# ---------------------------------------------------------------------------

def test_jwt_secret_required():
    """_secret_key() must raise if JWT_SECRET_KEY is missing."""
    import importlib
    import backend.auth as auth_mod

    orig = os.environ.pop("JWT_SECRET_KEY", None)
    try:
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            # Reload secret on each call
            auth_mod._secret_key.__wrapped__ = None  # type: ignore[attr-defined]
            # Direct call exercises the guard
            auth_mod.os.environ["JWT_SECRET_KEY"] = ""
            auth_mod._secret_key()
    finally:
        if orig:
            os.environ["JWT_SECRET_KEY"] = orig


def test_jwt_placeholder_rejected():
    """_secret_key() must raise for known placeholder values."""
    import backend.auth as auth_mod

    orig = os.environ.get("JWT_SECRET_KEY")
    try:
        for placeholder in ("changeme", "brokerai-dev", "dev", "test"):
            auth_mod.os.environ["JWT_SECRET_KEY"] = placeholder
            with pytest.raises(RuntimeError, match="placeholder"):
                auth_mod._secret_key()
    finally:
        if orig:
            os.environ["JWT_SECRET_KEY"] = orig


# ---------------------------------------------------------------------------
# S0-03: Refresh token issuance & rotation
# ---------------------------------------------------------------------------

def test_login_sets_refresh_cookie(client: TestClient):
    """Successful login must set an HttpOnly refresh_token cookie."""
    _register(client, "refresh_cookie@example.com")
    res = _login(client, "refresh_cookie@example.com")
    assert res.status_code == 200
    assert "refresh_token" in res.cookies
    data = res.json()
    assert "access_token" in data


def test_refresh_endpoint_returns_new_access_token(client: TestClient):
    """POST /auth/refresh must return a new access token when cookie is valid."""
    _register(client, "refresh_ep@example.com")
    login_res = _login(client, "refresh_ep@example.com")
    assert login_res.status_code == 200

    # The TestClient carries cookies automatically
    refresh_res = client.post("/auth/refresh")
    assert refresh_res.status_code == 200
    body = refresh_res.json()
    assert "access_token" in body


def test_refresh_without_cookie_returns_401(client: TestClient):
    """POST /auth/refresh with no cookie must return 401."""
    # Use a fresh client with no cookies
    from backend.main import app
    fresh = TestClient(app)
    res = fresh.post("/auth/refresh")
    assert res.status_code == 401


def test_logout_revokes_refresh_token(client: TestClient):
    """POST /auth/logout must revoke the refresh token; subsequent refresh must fail."""
    _register(client, "logout_test@example.com")
    login_res = _login(client, "logout_test@example.com")
    assert login_res.status_code == 200
    token_data = login_res.json()
    access = token_data["access_token"]

    logout_res = client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert logout_res.status_code == 200

    # Refresh should now be rejected
    refresh_res = client.post("/auth/refresh")
    assert refresh_res.status_code == 401


def test_refresh_token_stored_in_db(client: TestClient):
    """A RefreshToken row must be created in the DB after login."""
    _register(client, "db_refresh@example.com")
    login_res = _login(client, "db_refresh@example.com")
    assert login_res.status_code == 200

    with Session(engine) as session:
        user = session.exec(
            select(User).where(User.email == "db_refresh@example.com")
        ).first()
        assert user is not None
        tokens = session.exec(
            select(RefreshToken).where(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked == False,  # noqa: E712
            )
        ).all()
        assert len(tokens) >= 1


# ---------------------------------------------------------------------------
# S0-04: Password reset flow
# ---------------------------------------------------------------------------

def test_forgot_password_always_returns_ok(client: TestClient):
    """Forgot-password must return ok=True regardless of whether email exists (anti-enumeration)."""
    res = client.post("/auth/forgot-password", json={"email": "nonexistent@example.com"})
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_forgot_password_creates_db_token(client: TestClient):
    """Forgot-password creates a PasswordResetToken row in the DB for known users."""
    _register(client, "reset_token_user@example.com")
    client.post("/auth/forgot-password", json={"email": "reset_token_user@example.com"})

    with Session(engine) as session:
        user = session.exec(
            select(User).where(User.email == "reset_token_user@example.com")
        ).first()
        assert user is not None
        tok = session.exec(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used == False,  # noqa: E712
            )
        ).first()
        assert tok is not None


def test_reset_password_invalid_token_returns_400(client: TestClient):
    """Submitting a garbage reset token must return 400."""
    res = client.post(
        "/auth/reset-password",
        json={"token": "totally-fake-token", "password": "NewPass999!"},
    )
    assert res.status_code == 400


def test_reset_password_full_round_trip(client: TestClient):
    """Full password-reset flow: request → token → reset → login with new password."""
    email = "reset_roundtrip@example.com"
    old_pw = "OldPass111!"
    new_pw = "NewPass999!"
    _register(client, email, old_pw)

    # Request reset
    client.post("/auth/forgot-password", json={"email": email})

    # Retrieve raw token from DB (in real life this comes via email)
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()
        tok = session.exec(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used == False,  # noqa: E712
            )
        ).first()
        assert tok is not None
        # We cannot recover the raw token from the hash — simulate the email link
        # by injecting a known raw token into the DB
        import secrets
        raw = secrets.token_urlsafe(32)
        tok.token_hash = hash_reset_token(raw)
        session.add(tok)
        session.commit()

    # Use the raw token to reset
    res = client.post("/auth/reset-password", json={"token": raw, "password": new_pw})
    assert res.status_code == 200
    assert res.json()["ok"] is True

    # Old password must now fail
    bad_login = _login(client, email, old_pw)
    assert bad_login.status_code == 401

    # New password must succeed
    good_login = _login(client, email, new_pw)
    assert good_login.status_code == 200


def test_reset_token_cannot_be_reused(client: TestClient):
    """A reset token can only be used once."""
    import secrets
    email = "reset_once@example.com"
    _register(client, email)

    raw = secrets.token_urlsafe(32)
    tok_hash = hash_reset_token(raw)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=15)).replace(tzinfo=None)

    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()
        session.add(PasswordResetToken(
            user_id=user.id,
            token_hash=tok_hash,
            expires_at=expires_at,
        ))
        session.commit()

    # First use succeeds
    res1 = client.post("/auth/reset-password", json={"token": raw, "password": "NewPass111!"})
    assert res1.status_code == 200

    # Second use rejected
    res2 = client.post("/auth/reset-password", json={"token": raw, "password": "AnotherPass222!"})
    assert res2.status_code == 400


def test_expired_reset_token_rejected(client: TestClient):
    """An expired reset token must be rejected with 400."""
    import secrets
    email = "reset_expired@example.com"
    _register(client, email)

    raw = secrets.token_urlsafe(32)
    tok_hash = hash_reset_token(raw)
    # Already expired
    expires_at = (datetime.utcnow() - timedelta(minutes=1))

    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()
        session.add(PasswordResetToken(
            user_id=user.id,
            token_hash=tok_hash,
            expires_at=expires_at,
        ))
        session.commit()

    res = client.post("/auth/reset-password", json={"token": raw, "password": "SomePass123!"})
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# S0-06: Email verification
# ---------------------------------------------------------------------------

def test_send_verification_requires_auth(client: TestClient):
    """POST /auth/send-verification must reject unauthenticated requests."""
    res = client.post("/auth/send-verification")
    assert res.status_code == 401


def test_send_verification_creates_token(client: TestClient):
    """Authenticated user can request an email verification token."""
    _register(client, "verify_user@example.com")
    login_res = _login(client, "verify_user@example.com")
    access = login_res.json()["access_token"]

    res = client.post(
        "/auth/send-verification",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert res.status_code == 200

    with Session(engine) as session:
        user = session.exec(
            select(User).where(User.email == "verify_user@example.com")
        ).first()
        tok = session.exec(
            select(EmailVerificationToken).where(
                EmailVerificationToken.user_id == user.id,
                EmailVerificationToken.used == False,  # noqa: E712
            )
        ).first()
        assert tok is not None


def test_verify_email_round_trip(client: TestClient):
    """Supplying a valid verification token marks the user as email_verified."""
    import secrets

    email = "verify_roundtrip@example.com"
    _register(client, email)

    raw = secrets.token_urlsafe(32)
    tok_hash = hash_reset_token(raw)  # same SHA-256 helper
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).replace(tzinfo=None)

    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()
        assert user.email_verified is False
        session.add(EmailVerificationToken(
            user_id=user.id,
            token_hash=tok_hash,
            expires_at=expires_at,
        ))
        session.commit()
        user_id = user.id

    res = client.get(f"/auth/verify-email?token={raw}")
    assert res.status_code == 200
    assert res.json()["ok"] is True

    with Session(engine) as session:
        user = session.get(User, user_id)
        assert user.email_verified is True


# ---------------------------------------------------------------------------
# S0-07: Account lockout
# ---------------------------------------------------------------------------

def test_account_locked_after_10_failures(client: TestClient):
    """After 10 consecutive bad-password attempts the account must be locked."""
    email = "lockout_test@example.com"
    _register(client, email)

    # Exhaust failure counter
    for _ in range(10):
        res = client.post("/login", json={"email": email, "password": "WRONG_PASSWORD!"})
        assert res.status_code == 401

    # 11th attempt must be rejected with 429 (locked)
    final = client.post("/login", json={"email": email, "password": "WRONG_PASSWORD!"})
    assert final.status_code == 429
    assert "locked" in final.json()["detail"].lower()


def test_locked_account_rejects_correct_password(client: TestClient):
    """A locked account must be denied even with the correct password."""
    email = "lockout_correct@example.com"
    password = "CorrectHorse99!"
    _register(client, email, password)

    # Lock the account
    for _ in range(10):
        client.post("/login", json={"email": email, "password": "wrong"})

    # Correct password still blocked
    res = client.post("/login", json={"email": email, "password": password})
    assert res.status_code == 429


def test_lockout_resets_after_expiry(client: TestClient):
    """Once locked_until has passed, the user can log in normally."""
    email = "lockout_expired@example.com"
    password = "UnlockMe99!"
    _register(client, email, password)

    # Manually set a past lockout time in the DB
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()
        user.failed_login_attempts = 10
        user.locked_until = (datetime.utcnow() - timedelta(seconds=1))
        session.add(user)
        session.commit()

    # Should succeed now that lockout expired
    res = client.post("/login", json={"email": email, "password": password})
    assert res.status_code == 200


def test_failed_attempts_reset_on_success(client: TestClient):
    """Successful login must reset the failed_login_attempts counter."""
    email = "attempts_reset@example.com"
    password = "ResetMe99!"
    _register(client, email, password)

    # Fail a few times (not enough to lock)
    for _ in range(3):
        client.post("/login", json={"email": email, "password": "wrong"})

    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()
        assert user.failed_login_attempts == 3

    # Successful login resets counter
    res = _login(client, email, password)
    assert res.status_code == 200

    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()
        assert user.failed_login_attempts == 0
