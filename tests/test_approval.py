from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.helpers import create_test_user, mark_user_social_connected, stub_run_campaign_phase1, user_headers


def test_approve_campaign_returns_ok(client: TestClient) -> None:
    uid = create_test_user("appr@example.com", password="secret12")
    h = user_headers(uid)
    mark_user_social_connected(uid)
    body = {"goal": "Leads", "location": "Seattle", "platforms": ["Facebook"]}
    with patch("backend.main.run_campaign_phase1", side_effect=stub_run_campaign_phase1):
        cid = client.post("/generate-campaign", json=body, headers=h).json()["campaign_id"]

    with patch("backend.main.resume_campaign_publishing", return_value={}):
        r = client.post("/approve-campaign", json={"campaign_id": cid}, headers=h)
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_analytics_reflects_campaigns(client: TestClient) -> None:
    uid = create_test_user("an@example.com", password="secret12")
    h = user_headers(uid)
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
