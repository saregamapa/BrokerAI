from fastapi.testclient import TestClient


def test_signup_login_and_me(client: TestClient) -> None:
    r = client.post("/signup", json={"email": "auth1@example.com", "password": "secret12"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    r2 = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    j = r2.json()
    assert j["email"] == "auth1@example.com"
    assert j.get("social_connected") is False


def test_protected_route_without_token(client: TestClient) -> None:
    r = client.get("/me")
    assert r.status_code == 401


def test_login_invalid_password(client: TestClient) -> None:
    client.post("/signup", json={"email": "auth2@example.com", "password": "secret12"})
    r = client.post("/login", json={"email": "auth2@example.com", "password": "wrongpass"})
    assert r.status_code == 401
