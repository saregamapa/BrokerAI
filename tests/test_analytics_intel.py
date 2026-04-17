"""Analytics intelligence API: summary, posts table, bulk update, AI insights."""
from __future__ import annotations

from sqlmodel import Session

from backend.db import engine
from backend.models import Post


def test_analytics_summary_and_posts_empty(client):
    client.post(
        "/signup",
        json={"email": "intel0@example.com", "password": "secret12"},
    )
    token = client.post(
        "/login",
        json={"email": "intel0@example.com", "password": "secret12"},
    ).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    r = client.get("/analytics/summary", headers=h)
    assert r.status_code == 200
    assert r.json()["total_impressions"] == 0
    r2 = client.get("/analytics/posts", headers=h)
    assert r2.status_code == 200
    assert r2.json() == []


def test_analytics_update_and_posts_sorted(client):
    client.post(
        "/signup",
        json={"email": "intel1@example.com", "password": "secret12"},
    )
    token = client.post(
        "/login",
        json={"email": "intel1@example.com", "password": "secret12"},
    ).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    uid = client.get("/me", headers=h).json()["id"]
    with Session(engine) as s:
        s.add(
            Post(
                user_id=uid,
                campaign_id=None,
                caption="Alpha",
                content="Alpha",
                hashtags="[]",
                status="published",
                platform="linkedin",
                likes=0,
                comments=0,
                impressions=0,
                engagement_rate=0.0,
            )
        )
        s.add(
            Post(
                user_id=uid,
                campaign_id=None,
                caption="Beta",
                content="Beta",
                hashtags="[]",
                status="published",
                platform="instagram",
                likes=0,
                comments=0,
                impressions=0,
                engagement_rate=0.0,
            )
        )
        s.commit()

    up = client.post("/analytics/update", headers=h, json={})
    assert up.status_code == 200
    body = up.json()
    assert body["total"] == 2
    assert body["updated"] == 2

    rows = client.get("/analytics/posts", headers=h).json()
    assert len(rows) == 2
    assert rows[0]["engagement_rate"] >= rows[1]["engagement_rate"]

    summ = client.get("/analytics/summary", headers=h).json()
    assert summ["total_likes"] == 0
    assert summ["total_impressions"] == 0
    assert summ["published_count"] == 2


def test_analytics_insights_returns_shape(client):
    client.post(
        "/signup",
        json={"email": "intel2@example.com", "password": "secret12"},
    )
    token = client.post(
        "/login",
        json={"email": "intel2@example.com", "password": "secret12"},
    ).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    # analytics_ai requires growth plan or above — upgrade the user
    from sqlmodel import Session, select
    from backend.db import engine
    from backend.models import User
    with Session(engine) as s:
        u = s.exec(select(User).where(User.email == "intel2@example.com")).first()
        if u:
            u.plan = "growth"
            s.add(u)
            s.commit()
    r = client.get("/analytics/insights", headers=h)
    assert r.status_code == 200
    j = r.json()
    assert "insights" in j and isinstance(j["insights"], list)
    assert "mistakes" in j and "recommendations" in j and "next_post_ideas" in j
