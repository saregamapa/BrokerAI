"""
Google OAuth2 helper — exchange auth code for user profile.
No third-party OAuth libraries required (plain httpx).
"""
from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlencode

import httpx

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def get_google_auth_url(state: str = "") -> str:
    """Build the Google OAuth2 authorization URL."""
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    if state:
        params["state"] = state
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


async def exchange_code_for_profile(code: str) -> Optional[dict]:
    """Exchange authorization code for Google user profile. Returns None on failure."""
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "")

    async with httpx.AsyncClient(timeout=10) as client:
        # Step 1: Exchange code for tokens
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            return None
        tokens = token_resp.json()
        access_token = tokens.get("access_token")
        if not access_token:
            return None

        # Step 2: Get user info
        user_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if user_resp.status_code != 200:
            return None
        return user_resp.json()
        # Returns: {sub, email, name, picture, email_verified, ...}
