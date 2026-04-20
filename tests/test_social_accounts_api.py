"""Tests for /api/social-accounts and /api/webhooks/ayrshare."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import create_test_user, mark_user_social_connected, user_headers


def test_social_accounts_list_sync_disconnect(client: TestClient) -> None:
    uid = create_test_user("social_api_flow@example.com")
    mark_user_social_connected(uid)
    h = user_headers(uid)

    empty = client.get("/api/social-accounts", headers=h)
    assert empty.status_code == 200
    body0 = empty.json()
    assert body0.get("has_profile") is True
    assert body0.get("accounts") == []

    sync = client.post("/api/social-accounts/sync", headers=h)
    assert sync.status_code == 200
    synced = sync.json()
    assert synced.get("ok") is True
    assert len(synced.get("accounts") or []) >= 1

    listed = client.get("/api/social-accounts", headers=h).json()
    accs = listed["accounts"]
    assert len(accs) >= 1
    row_id = accs[0]["id"]

    disc = client.post("/api/social-accounts/disconnect", headers=h, json={"id": row_id})
    assert disc.status_code == 200

    after = client.get("/api/social-accounts", headers=h).json()
    by_id = {a["id"]: a["status"] for a in after["accounts"]}
    assert by_id[row_id] == "disconnected"


def test_social_accounts_disconnect_unknown(client: TestClient) -> None:
    uid = create_test_user("social_disc_404@example.com")
    h = user_headers(uid)
    r = client.post("/api/social-accounts/disconnect", headers=h, json={"id": 999999})
    assert r.status_code == 404


def test_social_webhook_ref_id_triggers_sync(client: TestClient, monkeypatch) -> None:
    called: list[int] = []

    def _sync(session, user_id: int):
        called.append(int(user_id))
        return {"ok": True, "accounts": [], "last_synced_at": None, "error": None}

    monkeypatch.setattr("backend.social_accounts_routes.sync_social_accounts_for_user", _sync)
    uid = create_test_user("social_webhook@example.com")
    r = client.post("/api/webhooks/ayrshare", json={"refId": f"brokerai_user_{uid}"})
    assert r.status_code == 200
    assert r.json().get("synced") is True
    assert called == [uid]


def test_social_connect_returns_jwt_url_when_generate_mocked(
    client: TestClient, monkeypatch
) -> None:
    uid = create_test_user("social_connect_jwt@example.com")
    mark_user_social_connected(uid)
    h = user_headers(uid)

    def _fake_jwt(profile_key: str, redirect_after_connect=None):
        assert "pytest-ayrshare" in (profile_key or "")
        return "https://example.ayrshare.test/sso?mock=1"

    monkeypatch.setattr(
        "backend.social_accounts_routes.generate_connect_jwt_url",
        _fake_jwt,
    )
    r = client.post("/api/social-accounts/connect", headers=h)
    assert r.status_code == 200
    assert r.json().get("url", "").startswith("https://example.ayrshare.test/")
