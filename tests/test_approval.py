from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.helpers import mark_user_social_connected, stub_run_campaign_phase1


def test_approve_campaign_returns_ok(client: TestClient) -> None:
    client.post("/signup", json={"email": "appr@example.com", "password": "secret12"})
    token = client.post("/login", json={"email": "appr@example.com", "password": "secret12"}).json()[
        "access_token"
    ]
    h = {"Authorization": f"Bearer {token}"}
    uid = client.get("/me", headers=h).json()["id"]
    mark_user_social_connected(uid)
    body = {"goal": "Leads", "location": "Seattle", "platforms": ["Facebook"]}
    with patch("backend.main.run_campaign_phase1", side_effect=stub_run_campaign_phase1):
        cid = client.post("/generate-campaign", json=body, headers=h).json()["campaign_id"]

    with patch("backend.main.resume_campaign_publishing", return_value={}):
        r = client.post("/approve-campaign", json={"campaign_id": cid}, headers=h)
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_analytics_reflects_campaigns(client: TestClient) -> None:
    client.post("/signup", json={"email": "an@example.com", "password": "secret12"})
    token = client.post("/login", json={"email": "an@example.com", "password": "secret12"}).json()[
        "access_token"
    ]
    h = {"Authorization": f"Bearer {token}"}
    uid = client.get("/me", headers=h).json()["id"]
    mark_user_social_connected(uid)
    body = {"goal": "Leads", "location": "Miami", "platforms": ["Facebook"]}
    with patch("backend.main.run_campaign_phase1", side_effect=stub_run_campaign_phase1):
        client.post("/generate-campaign", json=body, headers=h)
    r = client.get("/analytics", headers=h)
    assert r.status_code == 200
    j = r.json()
    assert j["total_campaigns"] >= 1
    assert j["total_posts"] >= 1
    assert "success_rate" in j
