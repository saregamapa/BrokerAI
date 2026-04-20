"""Exercise publish loop with Ayrshare call mocked."""
import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from backend.db import engine
from backend.models import Post
from backend.services import publish_service as publish_svc
from backend.services import scheduler as scheduler_mod
from tests.helpers import create_test_user, mark_user_social_connected


def test_publish_due_posts_marks_published(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    uid = create_test_user("sched@example.com", password="secret12")
    mark_user_social_connected(uid)

    with Session(engine) as s:
        p = Post(
            user_id=uid,
            campaign_id=None,
            caption="Scheduled post",
            hashtags="[]",
            status="approved",
            scheduled_at=datetime.utcnow() - timedelta(hours=1),
            publish_attempts=0,
        )
        s.add(p)
        s.commit()
        s.refresh(p)
        pid = p.id

    async def fake_publish(*args, **kwargs):
        return {"ok": True, "body": "mock"}

    monkeypatch.setattr(publish_svc, "publish_post", fake_publish)

    asyncio.run(scheduler_mod.publish_due_posts())

    with Session(engine) as s:
        row = s.get(Post, pid)
        assert row is not None
        assert row.status == "published"
        assert row.published_at is not None


def test_health_includes_status(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    j = r.json()
    assert j.get("status") == "ok"
    assert "openai_configured" in j
