"""Tests for /api/auth signup, login, JWT session, and plan assignment."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jose import jwt

from backend.auth import ALGORITHM


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_signup_assigns_plan_and_returns_jwt(client: TestClient) -> None:
    r = client.post(
        "/api/auth/signup",
        json={
            "name": "Plan User",
            "email": "plan_user@example.com",
            "password": "Secret123!",
            "planId": "pro",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("token_type") == "bearer"
    assert "access_token" in data and len(data["access_token"]) > 20
    user = data["user"]
    assert user["email"] == "plan_user@example.com"
    assert user["plan"] == "pro"
    assert user["plan_status"] in ("active", "trial")
    assert user.get("display_name") == "Plan User"


def test_signup_duplicate_email(client: TestClient) -> None:
    body = {
        "name": "A",
        "email": "dup_auth@example.com",
        "password": "Secret123!",
        "planId": "starter",
    }
    assert client.post("/api/auth/signup", json=body).status_code == 200
    r2 = client.post("/api/auth/signup", json=body)
    assert r2.status_code == 400
    assert "already exists" in (r2.json().get("detail") or "").lower()


def test_login_success_and_failure(client: TestClient) -> None:
    client.post(
        "/api/auth/signup",
        json={
            "name": "Login User",
            "email": "login_auth@example.com",
            "password": "GoodPass12!",
            "planId": "growth",
        },
    )
    ok = client.post(
        "/api/auth/login",
        json={"email": "login_auth@example.com", "password": "GoodPass12!"},
    )
    assert ok.status_code == 200
    assert ok.json()["user"]["email"] == "login_auth@example.com"

    bad = client.post(
        "/api/auth/login",
        json={"email": "login_auth@example.com", "password": "wrong-pass-xxx"},
    )
    assert bad.status_code == 401
    assert "invalid" in (bad.json().get("detail") or "").lower()


def test_me_requires_bearer_and_restores_session(client: TestClient) -> None:
    signup = client.post(
        "/api/auth/signup",
        json={
            "name": "Jwt User",
            "email": "jwt_me@example.com",
            "password": "Secret123!",
            "planId": "scale",
        },
    )
    token = signup.json()["access_token"]

    no = client.get("/me")
    assert no.status_code == 401

    me = client.get("/me", headers=_bearer(token))
    assert me.status_code == 200
    assert me.json()["email"] == "jwt_me@example.com"
    assert me.json()["plan"] == "scale"

    me_api = client.get("/api/auth/me", headers=_bearer(token))
    assert me_api.status_code == 200
    assert me_api.json()["plan_status"] in ("active", "trial")


def test_jwt_exp_claim_present(client: TestClient) -> None:
    import os

    r = client.post(
        "/api/auth/signup",
        json={
            "name": "Exp User",
            "email": "exp_claim@example.com",
            "password": "Secret123!",
            "planId": "starter",
        },
    )
    token = r.json()["access_token"]
    secret = os.environ["JWT_SECRET_KEY"]
    payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
    assert "exp" in payload and str(payload.get("sub")).isdigit()


def test_signup_trial_when_env_set(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROKERAI_TRIAL_DAYS", "14")
    r = client.post(
        "/api/auth/signup",
        json={
            "name": "Trial User",
            "email": "trial_user@example.com",
            "password": "Secret123!",
            "planId": "starter",
        },
    )
    assert r.status_code == 200
    assert r.json()["user"]["plan_status"] == "trial"
