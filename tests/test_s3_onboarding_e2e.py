"""
S3-12 — E2E: New user onboarding flow.

Covers the end-to-end journey a brand-new user takes:
  1. Signup
  2. Login → receive JWT
  3. GET /me → profile accessible
  4. GET /social-status → not connected (baseline)
  5. POST /connect-social (mock Ayrshare) → returns connect_url
  6. GET /social-status → now connected (after marking via helper)
  7. POST /generate-campaign → AI generation (LangGraph stubbed)
  8. GET /campaign/{id} → posts created
  9. GET /posts → posts visible
  10. GET /notifications → has a notification (seeded directly via service)
  11. GET /content-library → empty for new user
  12. POST /content-library → create a library item
  13. GET /content-library → item appears
  14. PATCH /notifications/{id}/read → marks read
  15. GET /notifications/unread-count → count decremented
  16. POST /notifications/read-all → all read
  17. GET /notifications/unread-count → 0
"""
from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.helpers import mark_user_social_connected, stub_run_campaign_phase1


# ---------------------------------------------------------------------------
# Shared flow state — single dict carries data across all test steps.
# ---------------------------------------------------------------------------
_FLOW: dict = {}


# ---------------------------------------------------------------------------
# Helper — register + login, return (headers, user_id)
# ---------------------------------------------------------------------------

def _signup_and_login(client: TestClient, email: str, password: str) -> tuple[dict, int]:
    r = client.post("/signup", json={"email": email, "password": password})
    assert r.status_code == 200, f"signup failed: {r.text}"
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    uid = client.get("/me", headers=headers).json()["id"]
    return headers, uid


# ---------------------------------------------------------------------------
# TestFlow — sequential class; each method depends on prior state via _FLOW.
# ---------------------------------------------------------------------------

class TestOnboardingFlow:
    """End-to-end onboarding journey for a brand-new user (S3-12)."""

    # Unique credentials so this test does not collide with any other test user.
    EMAIL = f"onboard_{uuid4().hex[:8]}@brokerai-test.dev"
    PASSWORD = "onboard-secret-99"

    # ------------------------------------------------------------------
    # Step 1 — Signup
    # ------------------------------------------------------------------
    def test_01_signup(self, client: TestClient) -> None:
        r = client.post("/signup", json={"email": self.EMAIL, "password": self.PASSWORD})
        assert r.status_code == 200, f"[Step 1] Signup failed: {r.text}"
        body = r.json()
        assert "access_token" in body, "[Step 1] No access_token in signup response"
        # Stash the token for subsequent steps
        _FLOW["token"] = body["access_token"]
        _FLOW["headers"] = {"Authorization": f"Bearer {_FLOW['token']}"}

    # ------------------------------------------------------------------
    # Step 2 — Login → receive JWT
    # ------------------------------------------------------------------
    def test_02_login(self, client: TestClient) -> None:
        r = client.post("/login", json={"email": self.EMAIL, "password": self.PASSWORD})
        assert r.status_code == 200, f"[Step 2] Login failed: {r.text}"
        body = r.json()
        assert "access_token" in body, "[Step 2] No access_token in login response"
        # Refresh stored token with the fresh one from login
        _FLOW["token"] = body["access_token"]
        _FLOW["headers"] = {"Authorization": f"Bearer {_FLOW['token']}"}

    # ------------------------------------------------------------------
    # Step 3 — GET /me → profile accessible
    # ------------------------------------------------------------------
    def test_03_get_me(self, client: TestClient) -> None:
        r = client.get("/me", headers=_FLOW["headers"])
        assert r.status_code == 200, f"[Step 3] GET /me failed: {r.text}"
        body = r.json()
        assert body["email"] == self.EMAIL, "[Step 3] Wrong email in /me response"
        assert "id" in body, "[Step 3] Missing 'id' in /me response"
        _FLOW["user_id"] = body["id"]

    # ------------------------------------------------------------------
    # Step 4 — GET /social-status → not connected (baseline)
    # ------------------------------------------------------------------
    def test_04_social_status_not_connected(self, client: TestClient) -> None:
        r = client.get("/social-status", headers=_FLOW["headers"])
        assert r.status_code == 200, f"[Step 4] GET /social-status failed: {r.text}"
        body = r.json()
        assert body["connected"] is False, "[Step 4] Expected connected=False for fresh user"
        assert body["state"] == "not_connected", "[Step 4] Expected state='not_connected'"

    # ------------------------------------------------------------------
    # Step 5 — POST /connect-social (mock Ayrshare) → returns connect_url
    # ------------------------------------------------------------------
    def test_05_connect_social(self, client: TestClient) -> None:
        # Patch fetch_profiles_by_ref_id to return [] so the route does not
        # hit the 409 "profile already exists" guard (conftest stubs it for the
        # standard pytest-ayrshare-profile-key user, but our unique user hasn't
        # registered a profile yet).
        with patch(
            "backend.main.fetch_profiles_by_ref_id",
            return_value=[],
        ), patch(
            "backend.main.create_ayrshare_profile",
            return_value="pytest-ayrshare-profile-key",
        ), patch(
            "backend.main.generate_social_connect_url",
            return_value="https://profile.ayrshare.com/mock-sso-jwt",
        ):
            r = client.post("/connect-social", json={}, headers=_FLOW["headers"])
        assert r.status_code == 200, f"[Step 5] POST /connect-social failed: {r.text}"
        body = r.json()
        assert "connect_url" in body, "[Step 5] Missing 'connect_url' in response"
        assert body["connect_url"], "[Step 5] connect_url is empty"

    # ------------------------------------------------------------------
    # Step 6 — GET /social-status → now connected (after DB helper)
    # ------------------------------------------------------------------
    def test_06_social_status_connected(self, client: TestClient) -> None:
        # Use the direct DB helper to simulate Ayrshare OAuth completion.
        mark_user_social_connected(_FLOW["user_id"])
        r = client.get("/social-status", headers=_FLOW["headers"])
        assert r.status_code == 200, f"[Step 6] GET /social-status failed: {r.text}"
        body = r.json()
        assert body["connected"] is True, "[Step 6] Expected connected=True after mark_user_social_connected"
        assert body["state"] == "connected", "[Step 6] Expected state='connected'"
        assert body["can_create_campaign"] is True, "[Step 6] Expected can_create_campaign=True"

    # ------------------------------------------------------------------
    # Step 7 — POST /generate-campaign → AI generation (LangGraph stubbed)
    # ------------------------------------------------------------------
    def test_07_generate_campaign(self, client: TestClient) -> None:
        body = {
            "goal": "Buyer leads",
            "location": "Austin, TX",
            "platforms": ["Facebook"],
            "frequency": "daily",
        }
        with patch("backend.main.run_campaign_phase1", side_effect=stub_run_campaign_phase1):
            r = client.post("/generate-campaign", json=body, headers=_FLOW["headers"])
        assert r.status_code == 200, f"[Step 7] POST /generate-campaign failed: {r.text}"
        data = r.json()
        assert "campaign_id" in data, "[Step 7] Missing campaign_id in response"
        assert "posts" in data, "[Step 7] Missing posts in response"
        assert len(data["posts"]) >= 1, "[Step 7] Expected at least 1 post after generation"
        _FLOW["campaign_id"] = data["campaign_id"]
        _FLOW["posts"] = data["posts"]

    # ------------------------------------------------------------------
    # Step 8 — GET /campaign/{id} → posts created
    # ------------------------------------------------------------------
    def test_08_get_campaign_detail(self, client: TestClient) -> None:
        cid = _FLOW["campaign_id"]
        r = client.get(f"/campaign/{cid}", headers=_FLOW["headers"])
        assert r.status_code == 200, f"[Step 8] GET /campaign/{cid} failed: {r.text}"
        body = r.json()
        assert body["campaign"]["id"] == cid, "[Step 8] campaign id mismatch"
        assert len(body["posts"]) >= 1, "[Step 8] Expected at least 1 post in campaign detail"

    # ------------------------------------------------------------------
    # Step 9 — GET /posts → posts visible for user
    # ------------------------------------------------------------------
    def test_09_list_posts(self, client: TestClient) -> None:
        r = client.get("/posts", headers=_FLOW["headers"])
        assert r.status_code == 200, f"[Step 9] GET /posts failed: {r.text}"
        posts = r.json()
        assert isinstance(posts, list), "[Step 9] Expected list of posts"
        assert len(posts) >= 1, "[Step 9] Expected at least 1 post for user"
        # Stash the first post id for later use
        _FLOW["post_id"] = posts[0]["id"]

    # ------------------------------------------------------------------
    # Step 10 — GET /notifications → has a notification (seeded directly)
    # ------------------------------------------------------------------
    def test_10_notifications_exist(self, client: TestClient) -> None:
        # Seed a notification directly via the service (generation does not create one;
        # notifications are created by publish_service or webhooks).
        from backend.services.notification_service import create_notification

        notif = create_notification(
            user_id=_FLOW["user_id"],
            type="campaign_generated",
            title="Campaign Ready",
            message="Your first campaign posts are ready for review.",
            action_url=f"/review.html?campaign={_FLOW['campaign_id']}",
        )
        _FLOW["notification_id"] = notif.id

        r = client.get("/notifications", headers=_FLOW["headers"])
        assert r.status_code == 200, f"[Step 10] GET /notifications failed: {r.text}"
        body = r.json()
        assert "notifications" in body, "[Step 10] Missing 'notifications' key"
        assert "unread_count" in body, "[Step 10] Missing 'unread_count' key"
        assert len(body["notifications"]) >= 1, "[Step 10] Expected at least 1 notification"
        # Verify our seeded notification is present and unread
        ids = [n["id"] for n in body["notifications"]]
        assert _FLOW["notification_id"] in ids, "[Step 10] Seeded notification not found in list"
        assert body["unread_count"] >= 1, "[Step 10] Expected unread_count >= 1"

    # ------------------------------------------------------------------
    # Step 11 — GET /content-library → empty for new user
    # ------------------------------------------------------------------
    def test_11_content_library_empty(self, client: TestClient) -> None:
        r = client.get("/content-library", headers=_FLOW["headers"])
        assert r.status_code == 200, f"[Step 11] GET /content-library failed: {r.text}"
        body = r.json()
        assert "items" in body, "[Step 11] Missing 'items' key in response"
        assert body["total"] == 0, f"[Step 11] Expected 0 items for new user, got {body['total']}"

    # ------------------------------------------------------------------
    # Step 12 — POST /content-library → create a library item
    # ------------------------------------------------------------------
    def test_12_create_content_library_item(self, client: TestClient) -> None:
        payload = {
            "kind": "caption",
            "content": "Just listed! This stunning 3BR/2BA home in Austin won't last long. #realestate",
            "label": "Listing announcement caption",
            "platform": "instagram",
            "tags": ["listing", "austin", "realestate"],
        }
        r = client.post("/content-library", json=payload, headers=_FLOW["headers"])
        assert r.status_code == 201, f"[Step 12] POST /content-library failed: {r.text}"
        body = r.json()
        assert "id" in body, "[Step 12] Missing 'id' in created item response"
        assert body["kind"] == "caption", "[Step 12] Wrong kind in created item"
        assert body["content"] == payload["content"], "[Step 12] Content mismatch"
        _FLOW["library_item_id"] = body["id"]

    # ------------------------------------------------------------------
    # Step 13 — GET /content-library → item appears
    # ------------------------------------------------------------------
    def test_13_content_library_has_item(self, client: TestClient) -> None:
        r = client.get("/content-library", headers=_FLOW["headers"])
        assert r.status_code == 200, f"[Step 13] GET /content-library failed: {r.text}"
        body = r.json()
        assert body["total"] == 1, f"[Step 13] Expected 1 item, got {body['total']}"
        item = body["items"][0]
        assert item["id"] == _FLOW["library_item_id"], "[Step 13] Wrong item id returned"
        assert item["kind"] == "caption", "[Step 13] Wrong item kind"

    # ------------------------------------------------------------------
    # Step 14 — PATCH /notifications/{id}/read → marks read
    # ------------------------------------------------------------------
    def test_14_mark_notification_read(self, client: TestClient) -> None:
        nid = _FLOW["notification_id"]
        r = client.patch(f"/notifications/{nid}/read", headers=_FLOW["headers"])
        assert r.status_code == 200, f"[Step 14] PATCH /notifications/{nid}/read failed: {r.text}"
        body = r.json()
        assert body.get("ok") is True, "[Step 14] Expected ok=True"

    # ------------------------------------------------------------------
    # Step 15 — GET /notifications/unread-count → count decremented
    # ------------------------------------------------------------------
    def test_15_unread_count_decremented(self, client: TestClient) -> None:
        r = client.get("/notifications/unread-count", headers=_FLOW["headers"])
        assert r.status_code == 200, f"[Step 15] GET /notifications/unread-count failed: {r.text}"
        body = r.json()
        assert "unread_count" in body, "[Step 15] Missing 'unread_count' key"
        # After marking the only notification as read, unread_count should be 0
        assert body["unread_count"] == 0, (
            f"[Step 15] Expected unread_count=0 after marking read, got {body['unread_count']}"
        )

    # ------------------------------------------------------------------
    # Step 16 — Seed a second notification and POST /notifications/read-all
    # ------------------------------------------------------------------
    def test_16_read_all_notifications(self, client: TestClient) -> None:
        from backend.services.notification_service import create_notification

        # Seed two more so read-all has something to do
        create_notification(
            user_id=_FLOW["user_id"],
            type="post_published",
            title="Post Published",
            message="Your post has been published successfully.",
        )
        create_notification(
            user_id=_FLOW["user_id"],
            type="post_published",
            title="Post Published",
            message="Another post has been published.",
        )

        # Confirm they show up as unread
        pre = client.get("/notifications/unread-count", headers=_FLOW["headers"])
        assert pre.status_code == 200
        assert pre.json()["unread_count"] >= 2, "[Step 16] Expected at least 2 unread before read-all"

        r = client.post("/notifications/read-all", headers=_FLOW["headers"])
        assert r.status_code == 200, f"[Step 16] POST /notifications/read-all failed: {r.text}"
        body = r.json()
        assert body.get("ok") is True, "[Step 16] Expected ok=True"
        assert body.get("marked_read", 0) >= 2, (
            f"[Step 16] Expected marked_read >= 2, got {body.get('marked_read')}"
        )

    # ------------------------------------------------------------------
    # Step 17 — GET /notifications/unread-count → 0
    # ------------------------------------------------------------------
    def test_17_unread_count_zero(self, client: TestClient) -> None:
        r = client.get("/notifications/unread-count", headers=_FLOW["headers"])
        assert r.status_code == 200, f"[Step 17] GET /notifications/unread-count failed: {r.text}"
        body = r.json()
        assert body["unread_count"] == 0, (
            f"[Step 17] Expected unread_count=0 after read-all, got {body['unread_count']}"
        )
