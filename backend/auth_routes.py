"""REST auth: /api/auth/* (signup, login, logout, me, forgot/reset password)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from backend.auth import create_access_token, get_current_user
from backend.db import get_session
from backend.models import User
from backend.schemas import (
    ApiAuthForgotPasswordRequest,
    ApiAuthResetPasswordRequest,
    ApiAuthSignupRequest,
    AuthSessionResponse,
    LoginRequest,
    UserOut,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _serialize_user(session: Session, u: User) -> UserOut:
    from backend import main as main_mod

    return main_mod._user_out(session, u)


@router.post("/signup", response_model=AuthSessionResponse)
def api_auth_signup(body: ApiAuthSignupRequest, session: Session = Depends(get_session)) -> AuthSessionResponse:
    from backend.services.auth_service import register_user

    try:
        u = register_user(
            session,
            name=body.name,
            email=body.email,
            password=body.password,
            plan_slug=body.plan_id,
        )
    except ValueError as e:
        if str(e) == "EMAIL_EXISTS":
            raise HTTPException(status_code=400, detail="Email already exists") from e
        raise HTTPException(status_code=400, detail="Could not create account") from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Something went wrong") from e

    token = create_access_token(int(u.id))
    return AuthSessionResponse(
        access_token=token,
        user=_serialize_user(session, u),
    )


@router.post("/login", response_model=AuthSessionResponse)
def api_auth_login(body: LoginRequest, session: Session = Depends(get_session)) -> AuthSessionResponse:
    from backend.services.auth_service import authenticate_user

    u, ok = authenticate_user(session, body.email, body.password)
    if not ok or u is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(int(u.id))
    return AuthSessionResponse(access_token=token, user=_serialize_user(session, u))


@router.post("/logout")
def api_auth_logout() -> dict:
    """Client clears JWT; included for API symmetry."""
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def api_auth_me(
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> UserOut:
    u = session.get(User, user.id)
    if u is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _serialize_user(session, u)


@router.post("/forgot-password")
def api_auth_forgot_password(
    body: ApiAuthForgotPasswordRequest,
    session: Session = Depends(get_session),
) -> dict:
    from backend.services.auth_service import request_password_reset

    request_password_reset(session, body.email)
    return {"ok": True}


@router.post("/reset-password")
def api_auth_reset_password(
    body: ApiAuthResetPasswordRequest,
    session: Session = Depends(get_session),
) -> dict:
    from backend.services.auth_service import reset_password_with_token

    try:
        reset_password_with_token(session, body.token.strip(), body.password)
    except ValueError as e:
        if str(e) == "INVALID_TOKEN":
            raise HTTPException(status_code=400, detail="Invalid or expired reset link") from e
        raise HTTPException(status_code=400, detail="Could not reset password") from e
    return {"ok": True}
