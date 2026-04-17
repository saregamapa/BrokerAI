"""S1-03: Unit tests for backend.core.plan_limits.

Tests cover:
  - Tier definitions and field values (sanity checks)
  - get_limits() lookup and fallback
  - is_plan_at_least() ordering
  - next_plan_up() progression
  - count_campaigns_this_month() — DB helper
  - assert_can_create_campaign() — enforcement
  - assert_platform_count() — enforcement
  - assert_feature() — enforcement
  - PlanLimitExceeded.to_response() structure
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest
from sqlmodel import Session

from backend.core.plan_limits import (
    PLAN_AGENCY,
    PLAN_FREE,
    PLAN_GROWTH,
    PLAN_ORDER,
    PLAN_PRO,
    PLAN_STARTER,
    UNLIMITED,
    PlanLimitExceeded,
    assert_can_create_campaign,
    assert_feature,
    assert_platform_count,
    count_campaigns_this_month,
    get_limits,
    is_plan_at_least,
    next_plan_up,
)
from backend.db import engine
from backend.models import Campaign, User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_user(plan: str = "free") -> User:
    """Create and persist a minimal User with the given plan."""
    import hashlib, secrets
    with Session(engine) as s:
        u = User(
            email=f"plan_test_{secrets.token_hex(6)}@example.com",
            password_hash=hashlib.sha256(b"x").hexdigest(),
            plan=plan,
        )
        s.add(u)
        s.commit()
        s.refresh(u)
        return u


def _add_campaigns(user_id: int, count: int) -> None:
    """Add `count` campaigns dated now (inside current month)."""
    with Session(engine) as s:
        for i in range(count):
            s.add(
                Campaign(
                    user_id=user_id,
                    name=f"Test Campaign {i}",
                    objective="Test",
                    target_audience="Everyone",
                    status="generated",
                    created_at=datetime.utcnow(),
                )
            )
        s.commit()


# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

class TestTierValues:
    def test_free_limits(self):
        assert PLAN_FREE.plan == "free"
        assert PLAN_FREE.campaigns_per_month == 2
        assert PLAN_FREE.posts_per_campaign == 5
        assert PLAN_FREE.platforms_allowed == 1
        assert PLAN_FREE.team_seats == 1
        assert PLAN_FREE.analytics_ai is False
        assert PLAN_FREE.comment_automations is False
        assert PLAN_FREE.custom_brand_kit is False
        assert PLAN_FREE.monthly_price_usd == 0

    def test_starter_limits(self):
        assert PLAN_STARTER.plan == "starter"
        assert PLAN_STARTER.campaigns_per_month == 10
        assert PLAN_STARTER.posts_per_campaign == 10
        assert PLAN_STARTER.platforms_allowed == 2
        assert PLAN_STARTER.team_seats == 1
        assert PLAN_STARTER.analytics_ai is False
        assert PLAN_STARTER.custom_brand_kit is True
        assert PLAN_STARTER.monthly_price_usd == 29

    def test_growth_limits(self):
        assert PLAN_GROWTH.plan == "growth"
        assert PLAN_GROWTH.campaigns_per_month == 30
        assert PLAN_GROWTH.team_seats == 3
        assert PLAN_GROWTH.analytics_ai is True
        assert PLAN_GROWTH.comment_automations is True
        assert PLAN_GROWTH.monthly_price_usd == 79

    def test_pro_limits(self):
        assert PLAN_PRO.plan == "pro"
        assert PLAN_PRO.campaigns_per_month == UNLIMITED
        assert PLAN_PRO.posts_per_campaign == UNLIMITED
        assert PLAN_PRO.platforms_allowed == UNLIMITED
        assert PLAN_PRO.team_seats == 10
        assert PLAN_PRO.analytics_ai is True
        assert PLAN_PRO.monthly_price_usd == 199

    def test_agency_limits(self):
        assert PLAN_AGENCY.plan == "agency"
        assert PLAN_AGENCY.campaigns_per_month == UNLIMITED
        assert PLAN_AGENCY.team_seats == UNLIMITED
        assert PLAN_AGENCY.monthly_price_usd is None  # contact sales

    def test_plan_order_integrity(self):
        assert PLAN_ORDER == ["free", "starter", "growth", "pro", "agency"]


# ---------------------------------------------------------------------------
# get_limits()
# ---------------------------------------------------------------------------

class TestGetLimits:
    def test_known_plans(self):
        for slug in PLAN_ORDER:
            lim = get_limits(slug)
            assert lim.plan == slug

    def test_unknown_plan_falls_back_to_free(self):
        lim = get_limits("unknown_tier")
        assert lim.plan == "free"

    def test_none_falls_back_to_free(self):
        lim = get_limits(None)  # type: ignore[arg-type]
        assert lim.plan == "free"

    def test_case_insensitive(self):
        assert get_limits("FREE").plan == "free"
        assert get_limits("Starter").plan == "starter"
        assert get_limits("GROWTH").plan == "growth"


# ---------------------------------------------------------------------------
# is_plan_at_least()
# ---------------------------------------------------------------------------

class TestIsPlanAtLeast:
    def test_same_tier_is_true(self):
        for plan in PLAN_ORDER:
            assert is_plan_at_least(plan, plan) is True

    def test_higher_tier_is_true(self):
        assert is_plan_at_least("growth", "starter") is True
        assert is_plan_at_least("pro", "free") is True
        assert is_plan_at_least("agency", "pro") is True

    def test_lower_tier_is_false(self):
        assert is_plan_at_least("free", "starter") is False
        assert is_plan_at_least("starter", "growth") is False
        assert is_plan_at_least("growth", "pro") is False

    def test_unknown_plan_is_false(self):
        assert is_plan_at_least("bogus", "free") is False
        assert is_plan_at_least("free", "bogus") is False


# ---------------------------------------------------------------------------
# next_plan_up()
# ---------------------------------------------------------------------------

class TestNextPlanUp:
    def test_free_goes_to_starter(self):
        n = next_plan_up("free")
        assert n is not None
        assert n.plan == "starter"

    def test_starter_goes_to_growth(self):
        assert next_plan_up("starter").plan == "growth"

    def test_growth_goes_to_pro(self):
        assert next_plan_up("growth").plan == "pro"

    def test_pro_goes_to_agency(self):
        assert next_plan_up("pro").plan == "agency"

    def test_agency_returns_none(self):
        assert next_plan_up("agency") is None

    def test_unknown_defaults_to_starter(self):
        # idx 0 (fallback) → next is index 1 = starter
        assert next_plan_up("nonexistent").plan == "starter"


# ---------------------------------------------------------------------------
# count_campaigns_this_month()
# ---------------------------------------------------------------------------

class TestCountCampaignsThisMonth:
    def test_zero_for_new_user(self):
        u = _make_user("free")
        with Session(engine) as s:
            count = count_campaigns_this_month(s, int(u.id))
        assert count == 0

    def test_counts_current_month_campaigns(self):
        u = _make_user("starter")
        _add_campaigns(int(u.id), 3)
        with Session(engine) as s:
            count = count_campaigns_this_month(s, int(u.id))
        assert count == 3

    def test_different_users_isolated(self):
        u1 = _make_user("free")
        u2 = _make_user("free")
        _add_campaigns(int(u1.id), 2)
        with Session(engine) as s:
            assert count_campaigns_this_month(s, int(u2.id)) == 0


# ---------------------------------------------------------------------------
# assert_can_create_campaign()
# ---------------------------------------------------------------------------

class TestAssertCanCreateCampaign:
    def test_free_user_allowed_up_to_limit(self):
        u = _make_user("free")
        _add_campaigns(int(u.id), 1)  # 1 of 2 used — OK
        with Session(engine) as s:
            u_db = s.get(User, u.id)
            assert_can_create_campaign(s, u_db)  # must not raise

    def test_free_user_blocked_at_limit(self):
        u = _make_user("free")
        _add_campaigns(int(u.id), 2)  # 2 of 2 — hit cap
        with Session(engine) as s:
            u_db = s.get(User, u.id)
            with pytest.raises(PlanLimitExceeded) as exc_info:
                assert_can_create_campaign(s, u_db)
        assert exc_info.value.limit_key == "campaigns_per_month"
        assert exc_info.value.current == 2
        assert exc_info.value.max_allowed == 2

    def test_starter_user_blocked_at_10(self):
        u = _make_user("starter")
        _add_campaigns(int(u.id), 10)
        with Session(engine) as s:
            u_db = s.get(User, u.id)
            with pytest.raises(PlanLimitExceeded):
                assert_can_create_campaign(s, u_db)

    def test_pro_user_never_blocked(self):
        u = _make_user("pro")
        _add_campaigns(int(u.id), 500)  # way over any normal limit
        with Session(engine) as s:
            u_db = s.get(User, u.id)
            assert_can_create_campaign(s, u_db)  # must not raise

    def test_unlimited_campaigns_env_bypasses_free_cap(self, monkeypatch):
        import secrets

        email = f"bypass_cap_{secrets.token_hex(4)}@example.com"
        monkeypatch.setenv("BROKERAI_UNLIMITED_CAMPAIGNS_EMAILS", email)
        with Session(engine) as s:
            u = User(
                email=email,
                password_hash="x",
                plan="free",
            )
            s.add(u)
            s.commit()
            s.refresh(u)
            _add_campaigns(int(u.id), 10)
            assert_can_create_campaign(s, s.get(User, u.id))


# ---------------------------------------------------------------------------
# assert_platform_count()
# ---------------------------------------------------------------------------

class TestAssertPlatformCount:
    def test_free_allows_one_platform(self):
        u = _make_user("free")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        assert_platform_count(u_db, ["instagram"])  # OK

    def test_free_blocks_two_platforms(self):
        u = _make_user("free")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        with pytest.raises(PlanLimitExceeded) as exc_info:
            assert_platform_count(u_db, ["instagram", "linkedin"])
        assert exc_info.value.limit_key == "platforms_allowed"
        assert exc_info.value.current == 2
        assert exc_info.value.max_allowed == 1

    def test_starter_allows_two_platforms(self):
        u = _make_user("starter")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        assert_platform_count(u_db, ["instagram", "linkedin"])  # OK

    def test_starter_blocks_three_platforms(self):
        u = _make_user("starter")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        with pytest.raises(PlanLimitExceeded):
            assert_platform_count(u_db, ["instagram", "linkedin", "twitter"])

    def test_pro_allows_any_count(self):
        u = _make_user("pro")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        assert_platform_count(u_db, ["instagram", "linkedin", "twitter", "tiktok", "facebook"])

    def test_deduplicates_platforms(self):
        """Duplicate platforms should count as one."""
        u = _make_user("free")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        assert_platform_count(u_db, ["instagram", "instagram"])  # deduped = 1, OK


# ---------------------------------------------------------------------------
# assert_feature()
# ---------------------------------------------------------------------------

class TestAssertFeature:
    def test_free_lacks_analytics_ai(self):
        u = _make_user("free")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        with pytest.raises(PlanLimitExceeded) as exc_info:
            assert_feature(u_db, "analytics_ai")
        assert exc_info.value.limit_key == "analytics_ai"

    def test_growth_has_analytics_ai(self):
        u = _make_user("growth")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        assert_feature(u_db, "analytics_ai")  # must not raise

    def test_free_lacks_comment_automations(self):
        u = _make_user("free")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        with pytest.raises(PlanLimitExceeded):
            assert_feature(u_db, "comment_automations")

    def test_starter_has_brand_kit(self):
        u = _make_user("starter")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        assert_feature(u_db, "custom_brand_kit")  # must not raise

    def test_free_lacks_brand_kit(self):
        u = _make_user("free")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        with pytest.raises(PlanLimitExceeded):
            assert_feature(u_db, "custom_brand_kit")


# ---------------------------------------------------------------------------
# PlanLimitExceeded.to_response()
# ---------------------------------------------------------------------------

class TestPlanLimitExceededResponse:
    def test_basic_fields(self):
        err = PlanLimitExceeded(
            "Over limit",
            limit_key="campaigns_per_month",
            current=5,
            max_allowed=5,
        )
        resp = err.to_response()
        assert resp["detail"] == "Over limit"
        assert resp["limit_key"] == "campaigns_per_month"
        assert resp["current"] == 5
        assert resp["max_allowed"] == 5
        assert resp["upgrade_required"] is True
        assert "upgrade_to" not in resp  # next_tier=None

    def test_with_next_tier(self):
        err = PlanLimitExceeded(
            "Need more campaigns",
            limit_key="campaigns_per_month",
            current=2,
            max_allowed=2,
            next_tier=PLAN_STARTER,
        )
        resp = err.to_response()
        assert resp["upgrade_to"] == "starter"
        assert resp["upgrade_price_usd"] == 29
