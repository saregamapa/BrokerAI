"""End-to-end smoke test of the critical user journey (no HTTP auth)."""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.helpers import create_test_user, mark_user_social_connected, stub_run_campaign_phase1, user_headers


def test_critical_user_journey(client: TestClient) -> None:
    email, password = "smoke@example.com", "smoke-pass-12"
    user_id = create_test_user(email, password=password)
    headers = user_headers(user_id)

    r = client.get("/me", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["email"] == email

    mark_user_social_connected(user_id)

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

    r = client.get(f"/campaign/{campaign_id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["campaign"]["id"] == campaign_id
    assert len(r.json()["posts"]) == len(posts)

    post_id = posts[0]["id"]
    r = client.post(f"/approve-post/{post_id}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"

    async def _fake_publish(pid: int, *, force_immediate: bool = False):
        return {"ok": True, "status": "published"}

    with patch("backend.main.safe_publish_post", side_effect=_fake_publish):
        r = client.post(f"/publish/{post_id}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["db"] == "ok"
    assert "version" in body and "uptime_seconds" in body


def test_me_without_token_returns_401(client: TestClient) -> None:
    create_test_user("smoke_first@example.com")
    r = client.get("/me")
    assert r.status_code == 401


def test_healthz_alias(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True
