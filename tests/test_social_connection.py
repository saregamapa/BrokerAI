from fastapi.testclient import TestClient
from sqlmodel import Session, select
from unittest.mock import patch

from backend.db import engine
from backend.models import SocialAccount
from tests.helpers import mark_user_social_connected


def _auth_headers(client: TestClient, email: str) -> tuple[dict, int]:
    client.post("/signup", json={"email": email, "password": "secret12"})
    token = client.post("/login", json={"email": email, "password": "secret12"}).json()[
        "access_token"
    ]
    h = {"Authorization": f"Bearer {token}"}
    uid = client.get("/me", headers=h).json()["id"]
    return h, uid


def test_social_status_source_of_truth_shape(client: TestClient) -> None:
    h, uid = _auth_headers(client, "soc1@example.com")
    r = client.get("/social-status", headers=h)
    assert r.status_code == 200
    j = r.json()
    assert j["connected"] is False
    assert j["state"] == "not_connected"
    assert j["profile_key_present"] is False
    assert j["can_create_campaign"] is False
    assert j["ayrshare_sync_ok"] is True

    mark_user_social_connected(uid)
    r2 = client.get("/social-status", headers=h)
    assert r2.status_code == 200
    j2 = r2.json()
    assert j2["connected"] is True
    assert j2["state"] == "connected"
    assert j2["profile_key_present"] is True
    assert j2["can_create_campaign"] is True


def test_social_connected_callback_updates_single_row(client: TestClient) -> None:
    h, uid = _auth_headers(client, "soc2@example.com")
    mark_user_social_connected(uid)

    a = client.post("/social-connected-callback", headers=h, json={})
    b = client.post("/social-connected-callback", headers=h, json={})
    assert a.status_code == 200
    assert b.status_code == 200
    assert a.json()["state"] == "connected"
    assert b.json()["state"] == "connected"

    with Session(engine) as s:
        rows = list(
            s.exec(
                select(SocialAccount).where(
                    SocialAccount.user_id == uid,
                    SocialAccount.platform == "ayrshare_profile",
                )
            ).all()
        )
    assert len(rows) == 1
    assert rows[0].is_connected is True


def test_connect_social_reuses_cached_profile_key_without_duplicate_create(
    client: TestClient,
) -> None:
    h, uid = _auth_headers(client, "soc3@example.com")
    with Session(engine) as s:
        row = s.exec(
            select(SocialAccount).where(
                SocialAccount.user_id == uid,
                SocialAccount.platform == "ayrshare_profile",
            )
        ).first()
        if row is None:
            row = SocialAccount(
                user_id=uid,
                platform="ayrshare_profile",
                is_connected=False,
                profile_key="pytest-ayrshare-profile-key",
            )
        else:
            row.is_connected = False
            row.profile_key = "pytest-ayrshare-profile-key"
        s.add(row)
        s.commit()

    with patch("backend.main.create_ayrshare_profile") as create_mock, patch(
        "backend.main.generate_social_connect_url",
        return_value="https://profile.ayrshare.com/mock-jwt",
    ):
        r = client.post("/connect-social", json={}, headers=h)
    assert r.status_code == 200
    assert "connect_url" in r.json()
    create_mock.assert_not_called()

