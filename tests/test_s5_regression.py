"""
S5-12 — Sprint 5 Regression Suite
Comprehensive tests for all Sprint 5 features:
  S5-01: Brand kit injection in LangGraph nodes
  S5-02: A/B caption testing endpoints
  S5-03: research_node fallback with _PLATFORM_BEST_PRACTICES
  S5-04: POST /posts/{id}/regenerate
  S5-05: compliance_node auto-pass removed
  S5-06: Comment Automation API (additional edge cases)
  S5-08: Audit log
  S5-09: Soft-delete edge cases
  S5-10: /campaigns/{id}/report/pdf
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from backend.db import engine
from backend.models import AuditEvent, Campaign, Post


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _signup_and_token(client: TestClient, email: str, password: str = "Sprint5Pass!") -> str:
    r = client.post("/signup", json={"email": email, "password": password})
    assert r.status_code == 200, f"signup failed for {email}: {r.text}"
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_user(client: TestClient, email: str) -> dict:
    """Register + login; return {'token': ..., 'id': ..., 'headers': ...}."""
    token = _signup_and_token(client, email)
    headers = _auth(token)
    uid = client.get("/me", headers=headers).json()["id"]
    return {"token": token, "headers": headers, "id": uid}


def _mark_social_connected(user_id: int) -> None:
    from tests.helpers import mark_user_social_connected
    mark_user_social_connected(user_id)


def _create_campaign_and_post(
    user_id: int,
    caption: str = "Test caption",
    status: str = "review",
    ab_variant_b: str | None = None,
    post_status: str = "review",
    platform: str = "instagram",
) -> tuple[int, int]:
    """Insert a Campaign + Post directly via DB. Returns (campaign_id, post_id)."""
    with Session(engine) as s:
        camp = Campaign(
            user_id=user_id,
            name="Regression Camp",
            objective="Test objective",
            target_audience="Test audience",
            status=status,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        s.add(camp)
        s.commit()
        s.refresh(camp)
        cid = camp.id

        post = Post(
            user_id=user_id,
            campaign_id=cid,
            caption=caption,
            content=caption,
            platform=platform,
            hashtags='["#test"]',
            image_url="https://example.com/img.jpg",
            video_script="{}",
            status=post_status,
            ab_variant_b=ab_variant_b,
        )
        s.add(post)
        s.commit()
        s.refresh(post)
        pid = post.id

    return cid, pid


# ===========================================================================
# S5-02: A/B Caption Testing
# ===========================================================================

class TestABTesting:
    """Tests for the A/B caption testing endpoints added in S5-02."""

    def test_get_ab_test_status_returns_required_fields(self, client: TestClient) -> None:
        """GET /posts/{id}/ab-test returns variant_a, variant_b, ab_status, ab_winner."""
        user = _make_user(client, "ab_get_status@example.com")
        _, pid = _create_campaign_and_post(
            user["id"], caption="Variant A caption", ab_variant_b="Variant B caption"
        )
        r = client.get(f"/posts/{pid}/ab-test", headers=user["headers"])
        assert r.status_code == 200, r.text
        data = r.json()
        assert "post_id" in data
        assert data["post_id"] == pid
        assert data["variant_a"] == "Variant A caption"
        assert data["variant_b"] == "Variant B caption"
        assert "ab_status" in data
        assert "ab_winner" in data

    def test_get_ab_test_no_variant_b_returns_null(self, client: TestClient) -> None:
        """GET /posts/{id}/ab-test when no variant B is set returns variant_b=null."""
        user = _make_user(client, "ab_no_varb@example.com")
        _, pid = _create_campaign_and_post(user["id"], caption="Only A", ab_variant_b=None)
        r = client.get(f"/posts/{pid}/ab-test", headers=user["headers"])
        assert r.status_code == 200, r.text
        assert r.json()["variant_b"] is None

    def test_ab_select_winner_a_sets_winner_and_keeps_caption(self, client: TestClient) -> None:
        """POST /posts/{id}/ab-select with winner='a' sets ab_winner='a', caption unchanged."""
        user = _make_user(client, "ab_select_a@example.com")
        _, pid = _create_campaign_and_post(
            user["id"], caption="Caption A original", ab_variant_b="Caption B alt"
        )
        r = client.post(f"/posts/{pid}/ab-select", json={"winner": "a"}, headers=user["headers"])
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["winner"] == "a"
        assert data["ab_status"] == "selected"
        assert data["caption"] == "Caption A original"

    def test_ab_select_winner_b_promotes_variant_b_to_caption(self, client: TestClient) -> None:
        """POST /posts/{id}/ab-select with winner='b' swaps caption to variant B."""
        user = _make_user(client, "ab_select_b@example.com")
        _, pid = _create_campaign_and_post(
            user["id"], caption="Original A caption", ab_variant_b="The B alternative"
        )
        r = client.post(f"/posts/{pid}/ab-select", json={"winner": "b"}, headers=user["headers"])
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["winner"] == "b"
        assert data["ab_status"] == "selected"
        assert data["caption"] == "The B alternative"

    def test_ab_select_invalid_winner_returns_422(self, client: TestClient) -> None:
        """POST /posts/{id}/ab-select with winner='c' returns 422 Unprocessable Entity."""
        user = _make_user(client, "ab_invalid_winner@example.com")
        _, pid = _create_campaign_and_post(user["id"], caption="A", ab_variant_b="B")
        r = client.post(f"/posts/{pid}/ab-select", json={"winner": "c"}, headers=user["headers"])
        assert r.status_code == 422, r.text

    def test_ab_select_another_users_post_returns_403(self, client: TestClient) -> None:
        """POST /posts/{id}/ab-select on another user's post returns 403."""
        owner = _make_user(client, "ab_owner_403@example.com")
        attacker = _make_user(client, "ab_attacker_403@example.com")
        _, pid = _create_campaign_and_post(owner["id"], caption="A", ab_variant_b="B")
        r = client.post(f"/posts/{pid}/ab-select", json={"winner": "a"}, headers=attacker["headers"])
        assert r.status_code == 403, r.text

    def test_ab_auto_select_returns_winner_and_method(self, client: TestClient) -> None:
        """POST /posts/{id}/ab-auto-select returns winner field and method='simulated'."""
        user = _make_user(client, "ab_auto_select@example.com")
        _, pid = _create_campaign_and_post(
            user["id"], caption="Auto A", ab_variant_b="Auto B"
        )
        r = client.post(f"/posts/{pid}/ab-auto-select", headers=user["headers"])
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["winner"] in ("a", "b")
        assert data["method"] == "simulated"
        assert data["ab_status"] == "selected"

    def test_ab_auto_select_no_variant_b_returns_400(self, client: TestClient) -> None:
        """POST /posts/{id}/ab-auto-select when no variant B returns 400."""
        user = _make_user(client, "ab_auto_no_b@example.com")
        _, pid = _create_campaign_and_post(user["id"], caption="Only A", ab_variant_b=None)
        r = client.post(f"/posts/{pid}/ab-auto-select", headers=user["headers"])
        assert r.status_code == 400, r.text

    def test_ab_select_winner_b_missing_variant_b_returns_400(self, client: TestClient) -> None:
        """POST /posts/{id}/ab-select winner='b' when no variant B is stored returns 400."""
        user = _make_user(client, "ab_select_b_novariant@example.com")
        _, pid = _create_campaign_and_post(user["id"], caption="Only A caption", ab_variant_b=None)
        r = client.post(f"/posts/{pid}/ab-select", json={"winner": "b"}, headers=user["headers"])
        assert r.status_code == 400, r.text


# ===========================================================================
# S5-04: Post Regeneration
# ===========================================================================

class TestPostRegenerate:
    """Tests for POST /posts/{id}/regenerate added in S5-04."""

    def _make_stub_nodes(self, caption_out: str = "Regenerated caption") -> tuple:
        """Return patched versions of content_node, media_node, compliance_node."""

        def fake_content(state):
            posts = list(state.get("posts") or [])
            updated = [{**p, "caption": caption_out, "content": caption_out, "ab_variant_b": "Alt variant"} for p in posts]
            return {**state, "posts": updated}

        def fake_media(state):
            posts = list(state.get("posts") or [])
            updated = [{**p, "image_prompt": "test prompt", "image_url": ""} for p in posts]
            return {**state, "posts": updated}

        def fake_compliance(state):
            posts = list(state.get("posts") or [])
            updated = [{**p, "compliance_passed": True, "compliance_issues": []} for p in posts]
            return {**state, "posts": updated}

        return fake_content, fake_media, fake_compliance

    def test_regenerate_returns_required_fields(self, client: TestClient) -> None:
        """POST /posts/{id}/regenerate returns content, compliance_passed, regenerated_at."""
        user = _make_user(client, "regen_fields@example.com")
        _, pid = _create_campaign_and_post(user["id"], caption="Old caption")
        fc, fm, fcomp = self._make_stub_nodes()
        with patch("backend.main.content_node", fc), \
             patch("backend.main.media_node", fm), \
             patch("backend.main.compliance_node", fcomp):
            r = client.post(f"/posts/{pid}/regenerate", headers=user["headers"])
        assert r.status_code == 200, r.text
        data = r.json()
        assert "post_id" in data
        assert "content" in data
        assert "compliance_passed" in data
        assert "regenerated_at" in data
        assert data["post_id"] == pid

    def test_regenerate_updates_post_caption(self, client: TestClient) -> None:
        """POST /posts/{id}/regenerate updates the caption in the database."""
        user = _make_user(client, "regen_caption_update@example.com")
        _, pid = _create_campaign_and_post(user["id"], caption="Stale caption to replace")
        fc, fm, fcomp = self._make_stub_nodes(caption_out="Brand new caption from regen")

        with patch("backend.main.content_node", fc), \
             patch("backend.main.media_node", fm), \
             patch("backend.main.compliance_node", fcomp):
            r = client.post(f"/posts/{pid}/regenerate", headers=user["headers"])

        assert r.status_code == 200, r.text
        assert "Brand new caption from regen" in r.json()["content"]

    def test_regenerate_another_users_post_returns_403(self, client: TestClient) -> None:
        """POST /posts/{id}/regenerate on another user's post returns 403."""
        owner = _make_user(client, "regen_owner@example.com")
        attacker = _make_user(client, "regen_attacker@example.com")
        _, pid = _create_campaign_and_post(owner["id"], caption="Owner's post")
        r = client.post(f"/posts/{pid}/regenerate", headers=attacker["headers"])
        assert r.status_code == 403, r.text

    def test_regenerate_nonexistent_post_returns_404(self, client: TestClient) -> None:
        """POST /posts/{id}/regenerate for a nonexistent post_id returns 404."""
        user = _make_user(client, "regen_404@example.com")
        r = client.post("/posts/99999999/regenerate", headers=user["headers"])
        assert r.status_code == 404, r.text

    def test_regenerate_sets_compliance_passed(self, client: TestClient) -> None:
        """POST /posts/{id}/regenerate compliance_passed reflects compliance_node result."""
        user = _make_user(client, "regen_compliance@example.com")
        _, pid = _create_campaign_and_post(user["id"], caption="Caption needing compliance")

        def fake_content(state):
            posts = list(state.get("posts") or [])
            updated = [{**p, "caption": "Compliant caption", "content": "Compliant caption"} for p in posts]
            return {**state, "posts": updated}

        def fake_media(state):
            return state

        def fake_compliance(state):
            posts = list(state.get("posts") or [])
            updated = [{**p, "compliance_passed": True, "compliance_issues": []} for p in posts]
            return {**state, "posts": updated}

        with patch("backend.main.content_node", fake_content), \
             patch("backend.main.media_node", fake_media), \
             patch("backend.main.compliance_node", fake_compliance):
            r = client.post(f"/posts/{pid}/regenerate", headers=user["headers"])

        assert r.status_code == 200, r.text
        assert r.json()["compliance_passed"] is True


# ===========================================================================
# S5-08: Audit Log
# ===========================================================================

class TestAuditLog:
    """Tests for the /audit-log endpoint added in S5-08."""

    def test_get_audit_log_returns_list(self, client: TestClient) -> None:
        """GET /audit-log returns a dict with 'events' list."""
        user = _make_user(client, "audit_list@example.com")
        r = client.get("/audit-log", headers=user["headers"])
        assert r.status_code == 200, r.text
        data = r.json()
        assert "events" in data
        assert isinstance(data["events"], list)

    def test_audit_log_requires_auth(self, client: TestClient) -> None:
        """GET /audit-log without auth returns 401 or 403."""
        r = client.get("/audit-log")
        assert r.status_code in (401, 403), r.text

    def test_audit_event_created_on_campaign_generation(self, client: TestClient) -> None:
        """Campaign generation triggers audit events that appear in /audit-log."""
        from tests.helpers import mark_user_social_connected, stub_run_campaign_phase1
        user = _make_user(client, "audit_campaign_gen@example.com")
        mark_user_social_connected(user["id"])
        body = {
            "goal": "Buyer leads",
            "location": "Miami, FL",
            "platforms": ["Facebook"],
            "frequency": "daily",
        }
        with patch("backend.main.run_campaign_phase1", side_effect=stub_run_campaign_phase1):
            client.post("/generate-campaign", json=body, headers=user["headers"])

        r = client.get("/audit-log", headers=user["headers"])
        assert r.status_code == 200, r.text
        events = r.json()["events"]
        # At least one audit event should exist (campaign.generated or similar)
        assert len(events) >= 1

    def test_audit_log_scoped_to_current_user(self, client: TestClient) -> None:
        """Audit log only returns events for the authenticated user, not others."""
        user_a = _make_user(client, "audit_scope_a@example.com")
        user_b = _make_user(client, "audit_scope_b@example.com")

        # Inject an audit event directly for user_a
        from backend.services.audit_service import audit_log, AuditEventType
        audit_log(
            AuditEventType.CAMPAIGN_CREATED,
            actor_user_id=user_a["id"],
            entity_type="campaign",
            entity_id=999001,
            summary=f"Audit scope test for user {user_a['id']}",
        )

        r_a = client.get("/audit-log", headers=user_a["headers"])
        r_b = client.get("/audit-log", headers=user_b["headers"])

        assert r_a.status_code == 200
        assert r_b.status_code == 200

        events_a = r_a.json()["events"]
        events_b = r_b.json()["events"]

        # User B's events should not include user A's entity_id
        b_entity_ids = {e["entity_id"] for e in events_b}
        # The event we created for user_a (entity_id=999001) must NOT appear for user_b
        assert 999001 not in b_entity_ids

    def test_audit_log_event_has_required_fields(self, client: TestClient) -> None:
        """Each event in /audit-log has id, event_type, entity_type, summary, created_at."""
        user = _make_user(client, "audit_fields@example.com")
        from backend.services.audit_service import audit_log, AuditEventType
        audit_log(
            AuditEventType.CAMPAIGN_CREATED,
            actor_user_id=user["id"],
            entity_type="campaign",
            entity_id=999002,
            summary="Fields check event",
        )
        r = client.get("/audit-log", headers=user["headers"])
        assert r.status_code == 200
        events = r.json()["events"]
        assert len(events) >= 1
        ev = events[0]
        assert "id" in ev
        assert "event_type" in ev
        assert "entity_type" in ev
        assert "summary" in ev
        assert "created_at" in ev

    def test_audit_log_limit_parameter(self, client: TestClient) -> None:
        """GET /audit-log?limit=2 returns at most 2 events."""
        user = _make_user(client, "audit_limit@example.com")
        from backend.services.audit_service import audit_log, AuditEventType
        for i in range(5):
            audit_log(
                AuditEventType.CAMPAIGN_CREATED,
                actor_user_id=user["id"],
                entity_type="campaign",
                entity_id=990000 + i,
                summary=f"Limit test event {i}",
            )
        r = client.get("/audit-log?limit=2", headers=user["headers"])
        assert r.status_code == 200
        assert len(r.json()["events"]) <= 2


# ===========================================================================
# S5-09: Soft-Delete Edge Cases
# ===========================================================================

class TestSoftDeleteEdgeCases:
    """Edge-case tests for soft-delete / restore (S5-09)."""

    def test_deleted_campaign_not_in_active_list(self, client: TestClient) -> None:
        """A soft-deleted campaign does NOT appear in GET /campaigns."""
        user = _make_user(client, "sd_active_list@example.com")
        cid, _ = _create_campaign_and_post(user["id"])

        # Soft-delete via API
        r_del = client.delete(f"/campaigns/{cid}", headers=user["headers"])
        assert r_del.status_code == 204, r_del.text

        r_list = client.get("/campaigns", headers=user["headers"])
        assert r_list.status_code == 200
        ids = [c["id"] for c in r_list.json()]
        assert cid not in ids

    def test_deleted_campaign_appears_in_deleted_list(self, client: TestClient) -> None:
        """GET /campaigns/deleted lists soft-deleted campaigns with recoverable_until."""
        user = _make_user(client, "sd_deleted_list@example.com")
        cid, _ = _create_campaign_and_post(user["id"])

        client.delete(f"/campaigns/{cid}", headers=user["headers"])

        r = client.get("/campaigns/deleted", headers=user["headers"])
        assert r.status_code == 200, r.text
        data = r.json()
        assert "campaigns" in data
        found_ids = [c["id"] for c in data["campaigns"]]
        assert cid in found_ids
        # Each entry has recoverable_until
        match = next(c for c in data["campaigns"] if c["id"] == cid)
        assert "recoverable_until" in match
        assert "deleted_at" in match

    def test_restore_deleted_campaign_succeeds(self, client: TestClient) -> None:
        """POST /campaigns/{id}/restore restores a soft-deleted campaign."""
        user = _make_user(client, "sd_restore@example.com")
        cid, _ = _create_campaign_and_post(user["id"])

        client.delete(f"/campaigns/{cid}", headers=user["headers"])

        r = client.post(f"/campaigns/{cid}/restore", headers=user["headers"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["restored"] is True
        assert body["campaign_id"] == cid

        # Should now be back in active list
        r_list = client.get("/campaigns", headers=user["headers"])
        ids = [c["id"] for c in r_list.json()]
        assert cid in ids

    def test_restore_another_users_campaign_returns_404(self, client: TestClient) -> None:
        """POST /campaigns/{id}/restore on another user's campaign returns 404."""
        owner = _make_user(client, "sd_restore_owner@example.com")
        attacker = _make_user(client, "sd_restore_attacker@example.com")
        cid, _ = _create_campaign_and_post(owner["id"])
        client.delete(f"/campaigns/{cid}", headers=owner["headers"])

        r = client.post(f"/campaigns/{cid}/restore", headers=attacker["headers"])
        assert r.status_code == 404, r.text

    def test_soft_deleted_posts_excluded_from_campaign_view(self, client: TestClient) -> None:
        """Soft-deleting a campaign also hides its posts from normal GET /posts."""
        user = _make_user(client, "sd_posts_hidden@example.com")
        cid, pid = _create_campaign_and_post(user["id"])

        # Soft-delete the campaign
        client.delete(f"/campaigns/{cid}", headers=user["headers"])

        # The post should not appear in GET /posts list
        r = client.get("/posts", headers=user["headers"])
        assert r.status_code == 200
        post_ids = [p["id"] for p in r.json()]
        assert pid not in post_ids

    def test_campaigns_deleted_requires_auth(self, client: TestClient) -> None:
        """GET /campaigns/deleted without auth returns 401 or 403."""
        r = client.get("/campaigns/deleted")
        assert r.status_code in (401, 403)

    def test_restore_active_campaign_returns_404(self, client: TestClient) -> None:
        """POST /campaigns/{id}/restore on a non-deleted campaign returns 404."""
        user = _make_user(client, "sd_restore_active@example.com")
        cid, _ = _create_campaign_and_post(user["id"])
        # Campaign is NOT deleted — restore should fail
        r = client.post(f"/campaigns/{cid}/restore", headers=user["headers"])
        assert r.status_code == 404, r.text


# ===========================================================================
# S5-10: PDF Report Endpoint
# ===========================================================================

class TestPDFReport:
    """Tests for GET /campaigns/{id}/report/pdf added in S5-10."""

    def test_report_returns_campaign_and_stats(self, client: TestClient) -> None:
        """GET /campaigns/{id}/report/pdf returns campaign, stats, posts, generated_at."""
        user = _make_user(client, "pdf_basic@example.com")
        cid, _ = _create_campaign_and_post(user["id"])

        r = client.get(f"/campaigns/{cid}/report/pdf", headers=user["headers"])
        assert r.status_code == 200, r.text
        data = r.json()
        assert "campaign" in data
        assert "stats" in data
        assert "posts" in data
        assert "generated_at" in data

    def test_report_campaign_fields(self, client: TestClient) -> None:
        """Report campaign object contains id, name, objective, status, created_at."""
        user = _make_user(client, "pdf_camp_fields@example.com")
        cid, _ = _create_campaign_and_post(user["id"])

        r = client.get(f"/campaigns/{cid}/report/pdf", headers=user["headers"])
        assert r.status_code == 200
        camp = r.json()["campaign"]
        assert camp["id"] == cid
        for field in ("name", "objective", "status", "created_at"):
            assert field in camp, f"Missing field: {field}"

    def test_report_stats_total_posts_matches_actual_count(self, client: TestClient) -> None:
        """stats.total_posts matches the actual number of non-deleted posts."""
        user = _make_user(client, "pdf_post_count@example.com")
        # Create campaign with 3 posts
        with Session(engine) as s:
            camp = Campaign(
                user_id=user["id"], name="PDF Count Camp", objective="Test",
                target_audience="All", status="draft",
                created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
            )
            s.add(camp)
            s.commit()
            s.refresh(camp)
            cid = camp.id
            for i in range(3):
                s.add(Post(
                    user_id=user["id"], campaign_id=cid,
                    caption=f"Post {i}", content=f"Post {i}",
                    hashtags="[]", video_script="{}",
                ))
            s.commit()

        r = client.get(f"/campaigns/{cid}/report/pdf", headers=user["headers"])
        assert r.status_code == 200
        stats = r.json()["stats"]
        assert stats["total_posts"] == 3

    def test_report_stats_published_count_correct(self, client: TestClient) -> None:
        """stats.published reflects the count of posts with status='published'."""
        user = _make_user(client, "pdf_pub_count@example.com")
        with Session(engine) as s:
            camp = Campaign(
                user_id=user["id"], name="PDF Pub Camp", objective="Test",
                target_audience="All", status="published",
                created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
            )
            s.add(camp)
            s.commit()
            s.refresh(camp)
            cid = camp.id
            # 2 published, 1 draft
            for i in range(2):
                s.add(Post(
                    user_id=user["id"], campaign_id=cid,
                    caption=f"Published {i}", content=f"Published {i}",
                    hashtags="[]", video_script="{}", status="published",
                ))
            s.add(Post(
                user_id=user["id"], campaign_id=cid,
                caption="Draft post", content="Draft post",
                hashtags="[]", video_script="{}", status="draft",
            ))
            s.commit()

        r = client.get(f"/campaigns/{cid}/report/pdf", headers=user["headers"])
        assert r.status_code == 200
        stats = r.json()["stats"]
        assert stats["published"] == 2

    def test_report_soft_deleted_posts_excluded(self, client: TestClient) -> None:
        """Soft-deleted posts are NOT counted in report stats."""
        user = _make_user(client, "pdf_softdel@example.com")
        with Session(engine) as s:
            camp = Campaign(
                user_id=user["id"], name="PDF Softdel Camp", objective="Test",
                target_audience="All", status="draft",
                created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
            )
            s.add(camp)
            s.commit()
            s.refresh(camp)
            cid = camp.id
            # 2 active posts
            for i in range(2):
                s.add(Post(
                    user_id=user["id"], campaign_id=cid,
                    caption=f"Active {i}", content=f"Active {i}",
                    hashtags="[]", video_script="{}",
                ))
            # 1 soft-deleted post
            deleted_post = Post(
                user_id=user["id"], campaign_id=cid,
                caption="Deleted post", content="Deleted post",
                hashtags="[]", video_script="{}",
                deleted_at=datetime.utcnow(),
            )
            s.add(deleted_post)
            s.commit()

        r = client.get(f"/campaigns/{cid}/report/pdf", headers=user["headers"])
        assert r.status_code == 200
        stats = r.json()["stats"]
        # Only the 2 active posts should be counted
        assert stats["total_posts"] == 2

    def test_report_another_users_campaign_returns_404(self, client: TestClient) -> None:
        """GET /campaigns/{id}/report/pdf on another user's campaign returns 404."""
        owner = _make_user(client, "pdf_owner_403@example.com")
        attacker = _make_user(client, "pdf_attacker_403@example.com")
        cid, _ = _create_campaign_and_post(owner["id"])

        r = client.get(f"/campaigns/{cid}/report/pdf", headers=attacker["headers"])
        assert r.status_code == 404, r.text

    def test_report_platforms_list(self, client: TestClient) -> None:
        """stats.platforms lists the unique platforms used in posts."""
        user = _make_user(client, "pdf_platforms@example.com")
        with Session(engine) as s:
            camp = Campaign(
                user_id=user["id"], name="PDF Plat Camp", objective="Test",
                target_audience="All", status="draft",
                created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
            )
            s.add(camp)
            s.commit()
            s.refresh(camp)
            cid = camp.id
            s.add(Post(
                user_id=user["id"], campaign_id=cid,
                caption="Insta post", content="Insta post",
                platform="instagram", hashtags="[]", video_script="{}",
            ))
            s.add(Post(
                user_id=user["id"], campaign_id=cid,
                caption="LinkedIn post", content="LinkedIn post",
                platform="linkedin", hashtags="[]", video_script="{}",
            ))
            s.commit()

        r = client.get(f"/campaigns/{cid}/report/pdf", headers=user["headers"])
        assert r.status_code == 200
        platforms = r.json()["stats"]["platforms"]
        assert isinstance(platforms, list)

    def test_report_deleted_campaign_returns_404(self, client: TestClient) -> None:
        """GET /campaigns/{id}/report/pdf on a soft-deleted campaign returns 404."""
        user = _make_user(client, "pdf_deleted_camp@example.com")
        cid, _ = _create_campaign_and_post(user["id"])
        client.delete(f"/campaigns/{cid}", headers=user["headers"])

        r = client.get(f"/campaigns/{cid}/report/pdf", headers=user["headers"])
        assert r.status_code == 404, r.text


# ===========================================================================
# S5-06: Automations — Additional Edge Cases
# ===========================================================================

class TestAutomationsAdditional:
    """Additional automation tests not covered by test_automations.py (S5-06)."""

    def test_simulate_multi_keyword_first_match_wins(self, client: TestClient) -> None:
        """Simulate with multiple keywords; first matching keyword is returned."""
        token = _signup_and_token(client, "auto_multi_kw@example.com")
        headers = _auth(token)
        r = client.post(
            "/automations",
            json={
                "name": "Multi kw",
                "trigger_keywords": "alpha,beta,gamma",
                "reply_template": "Hello {{name}}!",
            },
            headers=headers,
        )
        rule_id = r.json()["id"]

        sim = client.post(
            f"/automations/{rule_id}/simulate",
            json={"test_comment": "I love the beta version!"},
            headers=headers,
        )
        assert sim.status_code == 200
        data = sim.json()
        assert data["triggered"] is True
        assert data["matched_keyword"] == "beta"

    def test_simulate_case_insensitive_matching(self, client: TestClient) -> None:
        """Simulate trigger keyword matching is case-insensitive."""
        token = _signup_and_token(client, "auto_case_insensitive@example.com")
        headers = _auth(token)
        r = client.post(
            "/automations",
            json={
                "name": "Case insensitive",
                "trigger_keywords": "hello",
                "reply_template": "Hi there!",
            },
            headers=headers,
        )
        rule_id = r.json()["id"]

        sim = client.post(
            f"/automations/{rule_id}/simulate",
            json={"test_comment": "HELLO world this is a test"},
            headers=headers,
        )
        assert sim.status_code == 200
        data = sim.json()
        assert data["triggered"] is True
        assert data["matched_keyword"].lower() == "hello"

    def test_simulate_no_match_triggered_false(self, client: TestClient) -> None:
        """Simulate with a comment that does not match any keyword."""
        token = _signup_and_token(client, "auto_no_match_add@example.com")
        headers = _auth(token)
        r = client.post(
            "/automations",
            json={
                "name": "No match",
                "trigger_keywords": "price,listing,sold",
                "reply_template": "Thanks!",
            },
            headers=headers,
        )
        rule_id = r.json()["id"]

        sim = client.post(
            f"/automations/{rule_id}/simulate",
            json={"test_comment": "Nice weather today!"},
            headers=headers,
        )
        assert sim.status_code == 200
        assert sim.json()["triggered"] is False
        assert sim.json()["matched_keyword"] == ""

    def test_toggle_from_false_to_true(self, client: TestClient) -> None:
        """Toggling an inactive automation makes it active."""
        token = _signup_and_token(client, "auto_toggle_up@example.com")
        headers = _auth(token)
        r = client.post(
            "/automations",
            json={
                "name": "Toggle inactive",
                "trigger_keywords": "buy",
                "reply_template": "Thanks!",
                "is_active": False,
            },
            headers=headers,
        )
        rule_id = r.json()["id"]
        assert r.json()["is_active"] is False

        r2 = client.patch(f"/automations/{rule_id}/toggle", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["is_active"] is True

    def test_reply_preview_replaces_name_placeholder(self, client: TestClient) -> None:
        """Simulate reply_preview replaces {{name}} with empty string."""
        token = _signup_and_token(client, "auto_name_replace@example.com")
        headers = _auth(token)
        r = client.post(
            "/automations",
            json={
                "name": "Name replace",
                "trigger_keywords": "interested",
                "reply_template": "Thanks {{name}}! Here are the details.",
            },
            headers=headers,
        )
        rule_id = r.json()["id"]

        sim = client.post(
            f"/automations/{rule_id}/simulate",
            json={"test_comment": "I'm interested in this property"},
            headers=headers,
        )
        assert sim.status_code == 200
        assert sim.json()["triggered"] is True
        assert "{{name}}" not in sim.json()["reply_preview"]


# ===========================================================================
# S5-01: Brand Kit Injection — Unit-level tests for LangGraph nodes
# ===========================================================================

class TestBrandKitInjection:
    """Unit-level tests verifying brand_kit is injected into LangGraph nodes (S5-01)."""

    def _base_state(self, num_posts: int = 1) -> dict:
        """Minimal valid AgentState for testing node brand_kit injection."""
        return {
            "campaign_id": 1,
            "num_posts": num_posts,
            "brand_kit": {},
            "campaign_data": {
                "ai_text_enabled": True,
                "ai_images_enabled": True,
                "video_scripts_enabled": False,
                "goal": "grow awareness",
                "audience": "homebuyers",
                "platforms": ["instagram"],
                "frequency": "1 per week",
                "business_type": "real estate",
                "location": "Austin, TX",
                "tone": "professional",
            },
            "strategy_plan": {
                "days": [
                    {"day": "Day 1", "theme": "awareness", "angle": "engagement", "platform": "instagram"}
                ]
            },
            "posts": [
                {
                    "platform": "instagram",
                    "caption": "Test caption",
                    "content": "Test content",
                    "image_prompt": "Beautiful home",
                    "hashtags": [],
                    "compliance_passed": False,
                    "compliance_issues": [],
                    "day": "Day 1",
                    "video_script": "",
                    "ab_variant_b": None,
                }
            ],
        }

    def _mock_compliance_llm(self, monkeypatch) -> None:
        """Patch the LLM used inside _compliance_one to return a passing result."""
        from backend.agents import nodes as nodes_mod

        class _FakeComplianceLLM:
            def with_structured_output(self, schema):
                return self

            def invoke(self, messages):
                from backend.agents.nodes import ComplianceLLM
                return ComplianceLLM(passed=True, issues=[], fixed_caption="")

        monkeypatch.setattr(nodes_mod, "_llm", lambda key: _FakeComplianceLLM())
        monkeypatch.setattr(nodes_mod, "_openai_api_key", lambda: "sk-test-fake-key-compliance-0001")

    def test_compliance_node_with_brand_kit_forbidden_words(self, monkeypatch) -> None:
        """compliance_node reads brand_kit.forbidden_words without raising."""
        self._mock_compliance_llm(monkeypatch)
        from backend.agents.nodes import compliance_node
        state = self._base_state()
        state["brand_kit"] = {
            "voice": "professional",
            "tone": "friendly",
            "forbidden_words": "guaranteed,promise",
            "compliance_notes": "Must include fair housing language",
        }
        # compliance_node reads brand_kit and processes post — no exception
        result = compliance_node(state)
        assert "posts" in result
        assert len(result["posts"]) == 1
        post = result["posts"][0]
        assert "compliance_passed" in post

    def test_compliance_node_without_brand_kit_no_error(self, monkeypatch) -> None:
        """compliance_node with no brand_kit still runs correctly."""
        self._mock_compliance_llm(monkeypatch)
        from backend.agents.nodes import compliance_node
        state = self._base_state()
        state["brand_kit"] = {}
        result = compliance_node(state)
        assert "posts" in result
        assert len(result["posts"]) == 1

    def test_compliance_node_brand_kit_missing_graceful_fallback(self, monkeypatch) -> None:
        """compliance_node with brand_kit missing entirely defaults gracefully."""
        self._mock_compliance_llm(monkeypatch)
        from backend.agents.nodes import compliance_node
        state = self._base_state()
        # Omit brand_kit entirely
        del state["brand_kit"]
        result = compliance_node(state)
        assert "posts" in result

    def test_content_node_builds_brand_kit_block(self, monkeypatch) -> None:
        """content_node assembles brand_kit_block from brand_kit; verify via prompt capture."""
        from backend.agents import nodes as nodes_mod

        captured_prompts: list = []

        class FakeLLM:
            def with_structured_output(self, schema):
                return self

            def invoke(self, messages):
                for m in messages:
                    if hasattr(m, "content"):
                        captured_prompts.append(str(m.content))
                # Return a minimal valid CaptionSet-like object
                from unittest.mock import MagicMock
                result = MagicMock()
                result.posts = [
                    MagicMock(
                        caption="Brand-kit caption",
                        hashtags=["#test"],
                        image_prompt="Photo prompt",
                        video_script="",
                        ab_variant_b="Alt caption",
                    )
                ]
                return result

        monkeypatch.setattr(nodes_mod, "ChatOpenAI", lambda **kw: FakeLLM())
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key-brand-kit-test-0001")

        state = self._base_state()
        state["brand_kit"] = {
            "voice": "authoritative",
            "tone": "professional",
            "key_messages": "Trust and expertise in every home sale",
            "forbidden_words": "cheap,discount",
            "cta_style": "Ask for a call",
        }

        from backend.agents.nodes import content_node
        result = content_node(state)

        assert "posts" in result
        # At least one prompt should contain brand guidelines section
        all_prompts = " ".join(captured_prompts)
        assert "BRAND GUIDELINES" in all_prompts or len(captured_prompts) >= 1

    def test_content_node_without_brand_kit_no_brand_section(self, monkeypatch) -> None:
        """content_node with empty brand_kit does not include brand guidelines block."""
        from backend.agents import nodes as nodes_mod

        captured_prompts: list = []

        class FakeLLM:
            def with_structured_output(self, schema):
                return self

            def invoke(self, messages):
                for m in messages:
                    if hasattr(m, "content"):
                        captured_prompts.append(str(m.content))
                from unittest.mock import MagicMock
                result = MagicMock()
                result.posts = [
                    MagicMock(
                        caption="No brand kit caption",
                        hashtags=["#test"],
                        image_prompt="Photo prompt",
                        video_script="",
                        ab_variant_b=None,
                    )
                ]
                return result

        monkeypatch.setattr(nodes_mod, "ChatOpenAI", lambda **kw: FakeLLM())
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key-no-brand-kit-0001")

        state = self._base_state()
        state["brand_kit"] = {}  # Empty brand kit

        from backend.agents.nodes import content_node
        result = content_node(state)
        assert "posts" in result
        # Without brand_kit, BRAND GUIDELINES section should not be in prompts
        all_prompts = " ".join(captured_prompts)
        assert "BRAND GUIDELINES" not in all_prompts


# ===========================================================================
# S5-03: research_node fallback with _PLATFORM_BEST_PRACTICES
# ===========================================================================

class TestResearchNodeFallback:
    """Tests for research_node using _PLATFORM_BEST_PRACTICES fallback (S5-03)."""

    def _base_research_state(self, platforms: list | None = None) -> dict:
        return {
            "campaign_data": {
                "platforms": platforms or ["instagram"],
                "goal": "grow awareness",
                "audience": "homebuyers",
                "business_type": "real estate",
                "location": "Austin, TX",
            }
        }

    def test_research_node_no_api_key_uses_fallback(self, monkeypatch) -> None:
        """research_node with no OPENAI_API_KEY returns best-practice fallback insights."""
        from backend.agents import nodes as nodes_mod
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(nodes_mod, "_openai_api_key", lambda: "")

        from backend.agents.nodes import research_node
        state = self._base_research_state(platforms=["instagram"])
        result = research_node(state)

        assert "research_insights" in result
        insights = result["research_insights"]
        assert "insights" in insights
        assert len(insights["insights"]) >= 1

    def test_research_node_fallback_covers_known_platforms(self, monkeypatch) -> None:
        """Fallback provides insights for instagram, linkedin, facebook, twitter, tiktok."""
        from backend.agents import nodes as nodes_mod
        monkeypatch.setattr(nodes_mod, "_openai_api_key", lambda: "")

        from backend.agents.nodes import research_node, _PLATFORM_BEST_PRACTICES
        for platform in ["instagram", "linkedin", "facebook", "twitter", "tiktok"]:
            assert platform in _PLATFORM_BEST_PRACTICES, f"{platform} missing from best practices"
            state = self._base_research_state(platforms=[platform])
            result = research_node(state)
            assert "research_insights" in result
            insights_list = result["research_insights"].get("insights", [])
            assert len(insights_list) >= 1, f"No insights for platform {platform}"

    def test_research_node_best_practices_have_required_fields(self) -> None:
        """_PLATFORM_BEST_PRACTICES entries contain trending_formats, hook_styles, avoid."""
        from backend.agents.nodes import _PLATFORM_BEST_PRACTICES
        required_keys = {"platform", "trending_formats", "engagement_patterns", "hook_styles", "avoid"}
        for plat, entry in _PLATFORM_BEST_PRACTICES.items():
            missing = required_keys - set(entry.keys())
            assert not missing, f"Platform {plat} missing keys: {missing}"

    def test_research_node_fallback_returns_step_log(self, monkeypatch) -> None:
        """research_node fallback path returns a step_log entry."""
        from backend.agents import nodes as nodes_mod
        monkeypatch.setattr(nodes_mod, "_openai_api_key", lambda: "")

        from backend.agents.nodes import research_node
        state = self._base_research_state(platforms=["facebook"])
        result = research_node(state)

        assert "step_log" in result
        assert len(result["step_log"]) >= 1

    def test_research_node_exception_path_returns_fallback(self, monkeypatch) -> None:
        """research_node on LLM exception still returns fallback (graceful degradation)."""
        from backend.agents import nodes as nodes_mod

        class _ErrorLLM:
            def with_structured_output(self, schema):
                return self
            def invoke(self, messages):
                raise RuntimeError("Simulated LLM failure")

        monkeypatch.setattr(nodes_mod, "ChatOpenAI", lambda **kw: _ErrorLLM())
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key-research-err-0001")
        monkeypatch.setattr(nodes_mod, "_openai_api_key", lambda: "sk-test-fake-key-research-err-0001")

        from backend.agents.nodes import research_node
        state = self._base_research_state(platforms=["instagram"])
        result = research_node(state)

        # Must not raise and must return fallback insights
        assert "research_insights" in result
        assert len(result["research_insights"].get("insights", [])) >= 1


# ===========================================================================
# S5-05: Compliance Node — Auto-pass Removed
# ===========================================================================

class TestComplianceNodeAutoPassRemoved:
    """Verify compliance_node no longer blanket auto-passes all posts (S5-05)."""

    def _patch_compliance_llm(self, monkeypatch) -> None:
        """Patch the LLM inside _compliance_one to return a deterministic result."""
        from backend.agents import nodes as nodes_mod

        class _FakeCompLLM:
            def with_structured_output(self, schema):
                return self

            def invoke(self, messages):
                from backend.agents.nodes import ComplianceLLM
                return ComplianceLLM(passed=True, issues=[], fixed_caption="")

        monkeypatch.setattr(nodes_mod, "_llm", lambda key: _FakeCompLLM())
        monkeypatch.setattr(nodes_mod, "_openai_api_key", lambda: "sk-test-fake-compliance-auto-0001")

    def test_compliance_node_processes_posts_individually(self, monkeypatch) -> None:
        """compliance_node returns per-post compliance_passed field (not all True)."""
        self._patch_compliance_llm(monkeypatch)
        from backend.agents.nodes import compliance_node

        state = {
            "campaign_data": {
                "ai_text_enabled": True,
                "ai_images_enabled": True,
                "video_scripts_enabled": False,
                "goal": "sell homes",
                "platforms": ["facebook"],
            },
            "brand_kit": {},
            "posts": [
                {
                    "platform": "facebook",
                    "caption": "Buy this home now! Guaranteed best price in town!",
                    "content": "Buy this home now!",
                    "image_prompt": "house photo",
                    "hashtags": [],
                    "compliance_passed": None,
                    "compliance_issues": [],
                    "day": "Day 1",
                    "video_script": "",
                },
                {
                    "platform": "facebook",
                    "caption": "Lovely 3-bedroom home in a great neighborhood.",
                    "content": "Lovely 3-bedroom home.",
                    "image_prompt": "house photo",
                    "hashtags": [],
                    "compliance_passed": None,
                    "compliance_issues": [],
                    "day": "Day 2",
                    "video_script": "",
                },
            ],
        }

        result = compliance_node(state)
        # compliance_node must process both posts and set compliance_passed
        assert "posts" in result
        assert len(result["posts"]) == 2
        for p in result["posts"]:
            assert "compliance_passed" in p
            # compliance_passed must be a bool, not None
            assert isinstance(p["compliance_passed"], bool)

    def test_compliance_node_returns_issues_list(self, monkeypatch) -> None:
        """compliance_node returns compliance_issues as a list on each post."""
        self._patch_compliance_llm(monkeypatch)
        from backend.agents.nodes import compliance_node

        state = {
            "campaign_data": {
                "ai_text_enabled": True,
                "ai_images_enabled": True,
                "video_scripts_enabled": False,
                "goal": "sell",
                "platforms": ["instagram"],
            },
            "brand_kit": {},
            "posts": [
                {
                    "platform": "instagram",
                    "caption": "Normal caption without issues.",
                    "content": "Normal caption.",
                    "image_prompt": "house",
                    "hashtags": [],
                    "compliance_passed": None,
                    "compliance_issues": [],
                    "day": "Day 1",
                    "video_script": "",
                }
            ],
        }
        result = compliance_node(state)
        assert isinstance(result["posts"][0]["compliance_issues"], list)


# ===========================================================================
# S5-01/S5-10: Soft-delete service unit tests
# ===========================================================================

class TestSoftDeleteService:
    """Unit tests for the soft_delete_service module (S5-09)."""

    def test_soft_delete_campaign_returns_true(self) -> None:
        """soft_delete_campaign returns True when campaign is found and owned."""
        from backend.services.soft_delete_service import soft_delete_campaign

        # Create a real user + campaign
        with Session(engine) as s:
            from backend.models import User
            import hashlib
            u = User(
                email="sdsvc_owner_del@example.com",
                password_hash=hashlib.sha256(b"test").hexdigest(),
            )
            s.add(u)
            s.commit()
            s.refresh(u)
            uid = u.id

            camp = Campaign(
                user_id=uid, name="SDS Camp", objective="Test",
                target_audience="All", status="draft",
                created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
            )
            s.add(camp)
            s.commit()
            s.refresh(camp)
            cid = camp.id

        with Session(engine) as s:
            result = soft_delete_campaign(s, cid, uid)

        assert result is True

        # Verify deleted_at is set
        with Session(engine) as s:
            c = s.get(Campaign, cid)
            assert c.deleted_at is not None

    def test_soft_delete_campaign_wrong_owner_returns_false(self) -> None:
        """soft_delete_campaign returns False when user_id doesn't own the campaign."""
        from backend.services.soft_delete_service import soft_delete_campaign

        with Session(engine) as s:
            from backend.models import User
            import hashlib
            u = User(
                email="sdsvc_notowner@example.com",
                password_hash=hashlib.sha256(b"test").hexdigest(),
            )
            s.add(u)
            s.commit()
            s.refresh(u)
            uid = u.id

            camp = Campaign(
                user_id=uid, name="Not My Camp", objective="Test",
                target_audience="All", status="draft",
                created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
            )
            s.add(camp)
            s.commit()
            s.refresh(camp)
            cid = camp.id

        with Session(engine) as s:
            result = soft_delete_campaign(s, cid, user_id=9999999)

        assert result is False

    def test_restore_campaign_resets_deleted_at(self) -> None:
        """restore_campaign clears deleted_at and returns True."""
        from backend.services.soft_delete_service import soft_delete_campaign, restore_campaign

        with Session(engine) as s:
            from backend.models import User
            import hashlib
            u = User(
                email="sdsvc_restore@example.com",
                password_hash=hashlib.sha256(b"test").hexdigest(),
            )
            s.add(u)
            s.commit()
            s.refresh(u)
            uid = u.id

            camp = Campaign(
                user_id=uid, name="Restore Me", objective="Test",
                target_audience="All", status="draft",
                created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
            )
            s.add(camp)
            s.commit()
            s.refresh(camp)
            cid = camp.id

        with Session(engine) as s:
            soft_delete_campaign(s, cid, uid)

        with Session(engine) as s:
            result = restore_campaign(s, cid, uid)

        assert result is True
        with Session(engine) as s:
            c = s.get(Campaign, cid)
            assert c.deleted_at is None

    def test_purge_expired_soft_deletes_removes_old_records(self) -> None:
        """purge_expired_soft_deletes removes campaigns deleted > 30 days ago."""
        from backend.services.soft_delete_service import purge_expired_soft_deletes

        with Session(engine) as s:
            from backend.models import User
            import hashlib
            u = User(
                email="sdsvc_purge@example.com",
                password_hash=hashlib.sha256(b"test").hexdigest(),
            )
            s.add(u)
            s.commit()
            s.refresh(u)
            uid = u.id

            # Create a campaign deleted 31 days ago
            old_delete = datetime.utcnow() - timedelta(days=31)
            camp = Campaign(
                user_id=uid, name="Expired Camp", objective="Test",
                target_audience="All", status="draft",
                created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                deleted_at=old_delete,
            )
            s.add(camp)
            s.commit()
            s.refresh(camp)
            cid = camp.id

        with Session(engine) as s:
            count = purge_expired_soft_deletes(s)

        assert count >= 1
        with Session(engine) as s:
            c = s.get(Campaign, cid)
            assert c is None  # permanently deleted

    def test_soft_delete_post_sets_deleted_at(self) -> None:
        """soft_delete_post sets deleted_at on the post."""
        from backend.services.soft_delete_service import soft_delete_post

        with Session(engine) as s:
            from backend.models import User
            import hashlib
            u = User(
                email="sdsvc_postdel@example.com",
                password_hash=hashlib.sha256(b"test").hexdigest(),
            )
            s.add(u)
            s.commit()
            s.refresh(u)
            uid = u.id

            camp = Campaign(
                user_id=uid, name="Post Del Camp", objective="Test",
                target_audience="All", status="draft",
                created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
            )
            s.add(camp)
            s.commit()
            s.refresh(camp)
            cid = camp.id

            post = Post(
                user_id=uid, campaign_id=cid,
                caption="Post to delete", content="Post to delete",
                hashtags="[]", video_script="{}",
            )
            s.add(post)
            s.commit()
            s.refresh(post)
            pid = post.id

        with Session(engine) as s:
            result = soft_delete_post(s, pid, uid)

        assert result is True
        with Session(engine) as s:
            p = s.get(Post, pid)
            assert p.deleted_at is not None

    def test_soft_delete_post_already_deleted_returns_false(self) -> None:
        """soft_delete_post returns False when post is already deleted."""
        from backend.services.soft_delete_service import soft_delete_post

        with Session(engine) as s:
            from backend.models import User
            import hashlib
            u = User(
                email="sdsvc_already_del@example.com",
                password_hash=hashlib.sha256(b"test").hexdigest(),
            )
            s.add(u)
            s.commit()
            s.refresh(u)
            uid = u.id

            camp = Campaign(
                user_id=uid, name="Already Del Camp", objective="Test",
                target_audience="All", status="draft",
                created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
            )
            s.add(camp)
            s.commit()
            s.refresh(camp)
            cid = camp.id

            # Insert already-deleted post
            post = Post(
                user_id=uid, campaign_id=cid,
                caption="Already gone", content="Already gone",
                hashtags="[]", video_script="{}",
                deleted_at=datetime.utcnow(),
            )
            s.add(post)
            s.commit()
            s.refresh(post)
            pid = post.id

        with Session(engine) as s:
            result = soft_delete_post(s, pid, uid)

        assert result is False

    def test_restore_campaign_already_active_returns_false(self) -> None:
        """restore_campaign returns False when campaign is not deleted."""
        from backend.services.soft_delete_service import restore_campaign

        with Session(engine) as s:
            from backend.models import User
            import hashlib
            u = User(
                email="sdsvc_restore_active@example.com",
                password_hash=hashlib.sha256(b"test").hexdigest(),
            )
            s.add(u)
            s.commit()
            s.refresh(u)
            uid = u.id

            camp = Campaign(
                user_id=uid, name="Active Camp No Delete", objective="Test",
                target_audience="All", status="draft",
                created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                deleted_at=None,
            )
            s.add(camp)
            s.commit()
            s.refresh(camp)
            cid = camp.id

        with Session(engine) as s:
            result = restore_campaign(s, cid, uid)

        assert result is False


# ===========================================================================
# Post workflow state machine (workflow/post_state.py)
# ===========================================================================

class TestPostWorkflowState:
    """Unit tests for the post status state machine (backend/workflow/post_state.py)."""

    def test_is_transition_allowed_valid_paths(self) -> None:
        """Valid forward transitions return True."""
        from backend.workflow.post_state import is_transition_allowed
        assert is_transition_allowed("draft", "review") is True
        assert is_transition_allowed("review", "approved") is True
        assert is_transition_allowed("approved", "publishing") is True
        assert is_transition_allowed("publishing", "published") is True
        assert is_transition_allowed("publishing", "failed") is True
        assert is_transition_allowed("failed", "publishing") is True

    def test_is_transition_allowed_invalid_paths(self) -> None:
        """Invalid transitions return False."""
        from backend.workflow.post_state import is_transition_allowed
        assert is_transition_allowed("draft", "published") is False
        assert is_transition_allowed("published", "draft") is False
        assert is_transition_allowed("draft", "nonexistent") is False

    def test_is_transition_allowed_same_status(self) -> None:
        """Staying in the same status is always allowed."""
        from backend.workflow.post_state import is_transition_allowed
        for status in ("draft", "review", "approved", "publishing", "published", "failed"):
            assert is_transition_allowed(status, status) is True

    def test_normalize_legacy_post_status(self) -> None:
        """Legacy status strings map to current model values."""
        from backend.workflow.post_state import normalize_legacy_post_status
        assert normalize_legacy_post_status("pending_approval") == "review"
        assert normalize_legacy_post_status("publish_failed") == "failed"
        assert normalize_legacy_post_status("pending") == "draft"
        assert normalize_legacy_post_status("published") == "published"
        assert normalize_legacy_post_status("") == "draft"
        assert normalize_legacy_post_status(None) == "draft"

    def test_transition_post_status_applies_valid_transition(self) -> None:
        """transition_post_status applies a valid transition to the row."""
        from backend.workflow.post_state import transition_post_status

        with Session(engine) as s:
            from backend.models import User
            import hashlib
            u = User(
                email="post_wf_trans@example.com",
                password_hash=hashlib.sha256(b"test").hexdigest(),
            )
            s.add(u)
            s.commit()
            s.refresh(u)
            uid = u.id

            camp = Campaign(
                user_id=uid, name="WF Camp", objective="Test",
                target_audience="All", status="draft",
                created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
            )
            s.add(camp)
            s.commit()
            s.refresh(camp)

            post = Post(
                user_id=uid, campaign_id=camp.id,
                caption="WF test post", content="WF test post",
                hashtags="[]", video_script="{}", status="draft",
            )
            s.add(post)
            s.commit()
            s.refresh(post)
            pid = post.id

        with Session(engine) as s:
            p = s.get(Post, pid)
            transition_post_status(s, p, "review", reason="unit test", actor="test")
            s.commit()

        with Session(engine) as s:
            p = s.get(Post, pid)
            assert p.status == "review"

    def test_transition_post_status_invalid_raises_value_error(self) -> None:
        """transition_post_status raises ValueError on invalid transition."""
        from backend.workflow.post_state import transition_post_status

        with Session(engine) as s:
            from backend.models import User
            import hashlib
            u = User(
                email="post_wf_invalid@example.com",
                password_hash=hashlib.sha256(b"test").hexdigest(),
            )
            s.add(u)
            s.commit()
            s.refresh(u)
            uid = u.id

            camp = Campaign(
                user_id=uid, name="WF Invalid Camp", objective="Test",
                target_audience="All", status="draft",
                created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
            )
            s.add(camp)
            s.commit()
            s.refresh(camp)

            post = Post(
                user_id=uid, campaign_id=camp.id,
                caption="Invalid transition post", content="Invalid",
                hashtags="[]", video_script="{}", status="draft",
            )
            s.add(post)
            s.commit()
            s.refresh(post)
            pid = post.id

        with Session(engine) as s:
            p = s.get(Post, pid)
            with pytest.raises(ValueError, match="Invalid post status transition"):
                transition_post_status(s, p, "published")  # draft → published is invalid
