from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.helpers import create_test_user, mark_user_social_connected, stub_run_campaign_phase1, user_headers


def _auth_headers(client: TestClient, email: str = "camp@example.com") -> dict:
    uid = create_test_user(email, password="secret12")
    mark_user_social_connected(uid)
    return user_headers(uid)


def test_generate_campaign_and_fetch(client: TestClient) -> None:
    h = _auth_headers(client)
    body = {
        "goal": "Buyer leads",
        "location": "Austin, TX",
        "platforms": ["Facebook"],
        "frequency": "daily",
    }
    with patch("backend.main.run_campaign_phase1", side_effect=stub_run_campaign_phase1):
        r = client.post("/generate-campaign", json=body, headers=h)
    assert r.status_code == 200
    data = r.json()
    cid = data["campaign_id"]
    assert len(data["posts"]) >= 1
    r2 = client.get(f"/campaign/{cid}", headers=h)
    assert r2.status_code == 200
    assert r2.json()["campaign"]["id"] == cid
    assert len(r2.json()["posts"]) >= 1


def test_generate_campaign_without_social_creates_draft(client: TestClient) -> None:
    """Users with no Ayrshare account can still generate — campaign lands in draft status."""
    uid = create_test_user("nosoc@example.com", password="secret12")
    h = user_headers(uid)
    body = {"goal": "Leads", "location": "NYC", "platforms": ["Facebook"]}
    with patch("backend.main.run_campaign_phase1", side_effect=stub_run_campaign_phase1):
        r = client.post("/generate-campaign", json=body, headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["social_connected"] is False
    # Campaign should be in draft (not pending_approval) because no social key
    cid = data["campaign_id"]
    r2 = client.get(f"/campaign/{cid}", headers=h)
    assert r2.status_code == 200
    assert r2.json()["campaign"]["status"] == "draft"


def test_list_posts_after_generate(client: TestClient) -> None:
    h = _auth_headers(client, email="posts@example.com")
    body = {"goal": "Leads", "location": "Denver", "platforms": ["Facebook"]}
    with patch("backend.main.run_campaign_phase1", side_effect=stub_run_campaign_phase1):
        client.post("/generate-campaign", json=body, headers=h)
    r = client.get("/posts", headers=h)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1
