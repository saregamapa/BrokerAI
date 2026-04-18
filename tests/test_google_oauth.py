"""
S4-05: Tests for Google OAuth2 endpoints.

/auth/google       — redirects to Google when GOOGLE_CLIENT_ID is set;
                     returns 400 when not configured.
/auth/google/callback — handles error param, missing code, bad profile,
                        and the happy path (new user creation + JWT redirect).
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# /auth/google — redirect endpoint
# ---------------------------------------------------------------------------

def test_public_config_exposes_google_flag(client: TestClient):
    r = client.get("/api/public-config")
    assert r.status_code == 200
    data = r.json()
    assert "google_oauth_enabled" in data
    assert isinstance(data["google_oauth_enabled"], bool)


class TestGoogleAuthStart:
    def test_redirects_when_not_configured(self, client: TestClient):
        """No GOOGLE_CLIENT_ID → redirect to login with friendly notice (no JSON error page)."""
        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": ""}, clear=False):
            resp = client.get("/auth/google", follow_redirects=False)
        assert resp.status_code == 302
        loc = resp.headers.get("location", "")
        assert "login.html" in loc
        assert "google_oauth_unavailable" in loc

    def test_redirects_to_signup_when_referer_is_signup(self, client: TestClient):
        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": ""}, clear=False):
            resp = client.get(
                "/auth/google",
                headers={"Referer": "http://test/signup.html"},
                follow_redirects=False,
            )
        assert resp.status_code == 302
        assert "signup.html" in resp.headers["location"]
        assert "google_oauth_unavailable" in resp.headers["location"]

    def test_redirects_to_google_when_configured(self, client: TestClient):
        """Valid GOOGLE_CLIENT_ID → 307 redirect to accounts.google.com."""
        env_patch = {
            "GOOGLE_CLIENT_ID": "fake-client-id.apps.googleusercontent.com",
            "GOOGLE_REDIRECT_URI": "https://example.com/auth/google/callback",
        }
        with patch.dict(os.environ, env_patch, clear=False):
            resp = client.get("/auth/google", follow_redirects=False)
        assert resp.status_code in (302, 307)
        location = resp.headers["location"]
        assert "accounts.google.com" in location
        assert "fake-client-id" in location
        assert "openid" in location


# ---------------------------------------------------------------------------
# /auth/google/callback — callback endpoint
# ---------------------------------------------------------------------------

class TestGoogleAuthCallback:
    def test_error_param_redirects_to_login(self, client: TestClient):
        """Google sends error=access_denied → redirect to /login.html?error=..."""
        resp = client.get(
            "/auth/google/callback?error=access_denied",
            follow_redirects=False,
        )
        assert resp.status_code in (302, 307)
        assert "login.html" in resp.headers["location"]
        assert "google_auth_failed" in resp.headers["location"]

    def test_missing_code_redirects_to_login(self, client: TestClient):
        """No code param → redirect to /login.html?error=google_auth_failed."""
        resp = client.get("/auth/google/callback", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert "google_auth_failed" in resp.headers["location"]

    def test_exchange_failure_redirects_to_login(self, client: TestClient):
        """exchange_code_for_profile returns None → redirect to profile_failed."""
        with patch(
            "backend.services.google_oauth.exchange_code_for_profile",
            new=AsyncMock(return_value=None),
        ):
            resp = client.get(
                "/auth/google/callback?code=bad-code",
                follow_redirects=False,
            )
        assert resp.status_code in (302, 307)
        assert "google_profile_failed" in resp.headers["location"]

    def test_profile_missing_email_redirects(self, client: TestClient):
        """Profile returned without email → redirect to google_profile_failed."""
        with patch(
            "backend.services.google_oauth.exchange_code_for_profile",
            new=AsyncMock(return_value={"sub": "12345", "name": "No Email"}),
        ):
            resp = client.get(
                "/auth/google/callback?code=some-code",
                follow_redirects=False,
            )
        assert resp.status_code in (302, 307)
        assert "google_profile_failed" in resp.headers["location"]

    def test_new_user_created_and_redirected_to_dashboard(self, client: TestClient):
        """Valid profile for unknown email → creates user, returns HTML that sets JWT
        via localStorage (token never exposed in URL/browser history)."""
        fake_profile = {
            "sub": "google-uid-9999",
            "email": "googleuser@example.com",
            "name": "Google User",
            "picture": "https://lh3.googleusercontent.com/photo.jpg",
            "email_verified": True,
        }
        with patch(
            "backend.services.google_oauth.exchange_code_for_profile",
            new=AsyncMock(return_value=fake_profile),
        ):
            resp = client.get(
                "/auth/google/callback?code=valid-code",
                follow_redirects=False,
            )
        # Success path now returns 200 HTML (token set via localStorage, not URL fragment)
        assert resp.status_code == 200
        body = resp.text
        assert "brokerai_token" in body
        assert "dashboard.html" in body
        # Token embedded in script should be a non-empty JWT (3 dot-separated segments)
        import re
        token_match = re.search(r"localStorage\.setItem\('brokerai_token',\s*'([^']+)'", body)
        assert token_match, "JWT not found in HTML response"
        assert token_match.group(1).count(".") == 2

    def test_existing_user_gets_token(self, client: TestClient):
        """Existing user email → no duplicate created, still gets JWT via HTML response."""
        # First call creates the user
        fake_profile = {
            "sub": "google-uid-existing-1",
            "email": "existing-google@example.com",
            "name": "Existing User",
            "picture": "",
            "email_verified": True,
        }
        with patch(
            "backend.services.google_oauth.exchange_code_for_profile",
            new=AsyncMock(return_value=fake_profile),
        ):
            resp1 = client.get(
                "/auth/google/callback?code=code-1",
                follow_redirects=False,
            )
        # Success path returns 200 HTML with localStorage token injection
        assert resp1.status_code == 200
        assert "brokerai_token" in resp1.text

        # Second call (same email) — should still succeed, not error
        with patch(
            "backend.services.google_oauth.exchange_code_for_profile",
            new=AsyncMock(return_value=fake_profile),
        ):
            resp2 = client.get(
                "/auth/google/callback?code=code-2",
                follow_redirects=False,
            )
        assert resp2.status_code == 200
        assert "brokerai_token" in resp2.text
        assert "dashboard.html" in resp2.text
