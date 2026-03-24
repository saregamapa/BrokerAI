from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.helpers import stub_run_campaign_phase1


def _auth_headers(client: TestClient, email: str = "camp@example.com") -> dict:
    client.post("/signup", json={"email": email, "password": "secret12"})
    token = client.post("/login", json={"email": email, "password": "secret12"}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


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


def test_list_posts_after_generate(client: TestClient) -> None:
    h = _auth_headers(client, email="posts@example.com")
    body = {"goal": "Leads", "location": "Denver", "platforms": ["Facebook"]}
    with patch("backend.main.run_campaign_phase1", side_effect=stub_run_campaign_phase1):
        client.post("/generate-campaign", json=body, headers=h)
    r = client.get("/posts", headers=h)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1
