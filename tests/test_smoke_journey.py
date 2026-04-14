"""End-to-end smoke test of the critical user journey.

Signup → login → /me → generate campaign (LangGraph stubbed) → list posts
→ approve post → publish (Ayrshare stubbed) → /health sanity.

If this file fails, the product is broken for every user. Keep it green.
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.helpers import mark_user_social_connected, stub_run_campaign_phase1


def test_critical_user_journey(client: TestClient) -> None:
    email, password = "smoke@example.com", "smoke-pass-12"

    # 1. Signup
    r = client.post("/signup", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    assert "access_token" in r.json()

    # 2. Login (fresh token, proves round-trip)
    r = client.post("/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 3. /me
    r = client.get("/me", headers=headers)
    assert r.status_code == 200, r.text
    user_id = r.json()["id"]
    assert r.json()["email"] == email

    # 4. Mark social connected (bypass Ayrshare OAuth for the test)
    mark_user_social_connected(user_id)

    # 5. Generate campaign (LangGraph pipeline stubbed → 7 review posts)
    body = {
        "goal": "Buyer leads",
        "location": "Austin, TX",
        "platforms": ["Facebook"],
        "frequency": "daily",
    }
    with patch("backend.main.run_campaign_phase1", side_effect=stub_run_campaign_phase1):
        r = client.post("/generate-campaign", json=body, headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()
    campaign_id = data["campaign_id"]
    posts = data["posts"]
    assert len(posts) >= 1

    # 6. Fetch campaign + posts via /campaign/{id}
    r = client.get(f"/campaign/{campaign_id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["campaign"]["id"] == campaign_id
    assert len(r.json()["posts"]) == len(posts)

    # 7. Approve the first post (review → approved)
    post_id = posts[0]["id"]
    r = client.post(f"/approve-post/{post_id}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"

    # 8. Publish the approved post — Ayrshare is mocked so no real HTTP call
    async def _fake_publish(pid: int, *, force_immediate: bool = False):
        return {"ok": True, "status": "published"}

    with patch("backend.main.safe_publish_post", side_effect=_fake_publish):
        r = client.post(f"/publish/{post_id}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    # 9. /health is live and DB ping passes
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["db"] == "ok"
    assert "version" in body and "uptime_seconds" in body


def test_login_rejects_bad_password(client: TestClient) -> None:
    client.post("/signup", json={"email": "badpass@example.com", "password": "correct-horse"})
    r = client.post("/login", json={"email": "badpass@example.com", "password": "wrong-horse"})
    assert r.status_code == 401


def test_me_requires_token(client: TestClient) -> None:
    assert client.get("/me").status_code == 401


def test_healthz_alias(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True
