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
    PLAN_GROWTH,
    PLAN_ORDER,
    PLAN_PRO,
    PLAN_SCALE,
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

def _make_user(plan: str = "starter") -> User:
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
    def test_starter_limits(self):
        assert PLAN_STARTER.plan == "starter"
        assert PLAN_STARTER.campaigns_per_month == 1
        assert PLAN_STARTER.posts_per_campaign == 10
        assert PLAN_STARTER.platforms_allowed == 2
        assert PLAN_STARTER.team_seats == 1
        assert PLAN_STARTER.videos_per_month == 1
        assert PLAN_STARTER.analytics_ai is True
        assert PLAN_STARTER.comment_automations is False
        assert PLAN_STARTER.custom_brand_kit is True
        assert PLAN_STARTER.monthly_price_usd == 29

    def test_growth_limits(self):
        assert PLAN_GROWTH.plan == "growth"
        assert PLAN_GROWTH.campaigns_per_month == 5
        assert PLAN_GROWTH.posts_per_campaign == 30
        assert PLAN_GROWTH.platforms_allowed == 5
        assert PLAN_GROWTH.team_seats == 1
        assert PLAN_GROWTH.videos_per_month == 5
        assert PLAN_GROWTH.analytics_ai is True
        assert PLAN_GROWTH.comment_automations is False
        assert PLAN_GROWTH.monthly_price_usd == 79

    def test_pro_limits(self):
        assert PLAN_PRO.plan == "pro"
        assert PLAN_PRO.campaigns_per_month == 10
        assert PLAN_PRO.posts_per_campaign == 60
        assert PLAN_PRO.platforms_allowed == 10
        assert PLAN_PRO.team_seats == 3
        assert PLAN_PRO.videos_per_month == 10
        assert PLAN_PRO.analytics_ai is True
        assert PLAN_PRO.comment_automations is True
        assert PLAN_PRO.monthly_price_usd == 259

    def test_scale_limits(self):
        assert PLAN_SCALE.plan == "scale"
        assert PLAN_SCALE.campaigns_per_month == 20
        assert PLAN_SCALE.posts_per_campaign == 120
        assert PLAN_SCALE.platforms_allowed == 15
        assert PLAN_SCALE.team_seats == 5
        assert PLAN_SCALE.videos_per_month == 15
        assert PLAN_SCALE.analytics_ai is True
        assert PLAN_SCALE.comment_automations is True
        assert PLAN_SCALE.monthly_price_usd == 399

    def test_agency_limits(self):
        assert PLAN_AGENCY.plan == "agency"
        assert PLAN_AGENCY.campaigns_per_month == UNLIMITED
        assert PLAN_AGENCY.team_seats == UNLIMITED
        assert PLAN_AGENCY.monthly_price_usd is None  # contact sales

    def test_plan_order_integrity(self):
        assert PLAN_ORDER == ["starter", "growth", "pro", "scale", "agency"]


# ---------------------------------------------------------------------------
# get_limits()
# ---------------------------------------------------------------------------

class TestGetLimits:
    def test_known_plans(self):
        for slug in PLAN_ORDER:
            lim = get_limits(slug)
            assert lim.plan == slug

    def test_unknown_plan_falls_back_to_starter(self):
        lim = get_limits("unknown_tier")
        assert lim.plan == "starter"

    def test_none_falls_back_to_starter(self):
        lim = get_limits(None)  # type: ignore[arg-type]
        assert lim.plan == "starter"

    def test_case_insensitive(self):
        assert get_limits("STARTER").plan == "starter"
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
        assert is_plan_at_least("pro", "starter") is True
        assert is_plan_at_least("agency", "pro") is True

    def test_lower_tier_is_false(self):
        assert is_plan_at_least("starter", "growth") is False
        assert is_plan_at_least("growth", "pro") is False
        assert is_plan_at_least("pro", "scale") is False

    def test_unknown_plan_is_false(self):
        assert is_plan_at_least("bogus", "starter") is False
        assert is_plan_at_least("starter", "bogus") is False


# ---------------------------------------------------------------------------
# next_plan_up()
# ---------------------------------------------------------------------------

class TestNextPlanUp:
    def test_starter_goes_to_growth(self):
        assert next_plan_up("starter").plan == "growth"

    def test_growth_goes_to_pro(self):
        assert next_plan_up("growth").plan == "pro"

    def test_pro_goes_to_scale(self):
        assert next_plan_up("pro").plan == "scale"

    def test_scale_goes_to_agency(self):
        assert next_plan_up("scale").plan == "agency"

    def test_agency_returns_none(self):
        assert next_plan_up("agency") is None

    def test_unknown_defaults_to_growth(self):
        # idx 0 (fallback) is starter → next is index 1 = growth
        assert next_plan_up("nonexistent").plan == "growth"


# ---------------------------------------------------------------------------
# count_campaigns_this_month()
# ---------------------------------------------------------------------------

class TestCountCampaignsThisMonth:
    def test_zero_for_new_user(self):
        u = _make_user("starter")
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
        u1 = _make_user("starter")
        u2 = _make_user("starter")
        _add_campaigns(int(u1.id), 2)
        with Session(engine) as s:
            assert count_campaigns_this_month(s, int(u2.id)) == 0


# ---------------------------------------------------------------------------
# assert_can_create_campaign()
# ---------------------------------------------------------------------------

class TestAssertCanCreateCampaign:
    def test_starter_user_blocked_at_1(self):
        u = _make_user("starter")
        _add_campaigns(int(u.id), 1)
        with Session(engine) as s:
            u_db = s.get(User, u.id)
            with pytest.raises(PlanLimitExceeded) as exc_info:
                assert_can_create_campaign(s, u_db)
        assert exc_info.value.limit_key == "campaigns_per_month"
        assert exc_info.value.current == 1
        assert exc_info.value.max_allowed == 1

    def test_starter_user_allowed_at_0(self):
        u = _make_user("starter")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
            assert_can_create_campaign(s, u_db)  # must not raise

    def test_growth_user_blocked_at_5(self):
        u = _make_user("growth")
        _add_campaigns(int(u.id), 5)
        with Session(engine) as s:
            u_db = s.get(User, u.id)
            with pytest.raises(PlanLimitExceeded):
                assert_can_create_campaign(s, u_db)

    def test_pro_user_blocked_at_10(self):
        u = _make_user("pro")
        _add_campaigns(int(u.id), 10)
        with Session(engine) as s:
            u_db = s.get(User, u.id)
            with pytest.raises(PlanLimitExceeded):
                assert_can_create_campaign(s, u_db)

    def test_scale_user_blocked_at_20(self):
        u = _make_user("scale")
        _add_campaigns(int(u.id), 20)
        with Session(engine) as s:
            u_db = s.get(User, u.id)
            with pytest.raises(PlanLimitExceeded):
                assert_can_create_campaign(s, u_db)

    def test_scale_user_allowed_up_to_19(self):
        u = _make_user("scale")
        _add_campaigns(int(u.id), 19)
        with Session(engine) as s:
            u_db = s.get(User, u.id)
            assert_can_create_campaign(s, u_db)  # must not raise

    def test_agency_user_never_blocked(self):
        u = _make_user("agency")
        _add_campaigns(int(u.id), 500)  # way over any normal limit
        with Session(engine) as s:
            u_db = s.get(User, u.id)
            assert_can_create_campaign(s, u_db)  # must not raise

    def test_unlimited_campaigns_env_bypasses_cap(self, monkeypatch):
        import secrets

        email = f"bypass_cap_{secrets.token_hex(4)}@example.com"
        monkeypatch.setenv("BROKERAI_UNLIMITED_CAMPAIGNS_EMAILS", email)
        with Session(engine) as s:
            u = User(
                email=email,
                password_hash="x",
                plan="starter",
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
    def test_starter_allows_two_platforms(self):
        u = _make_user("starter")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        assert_platform_count(u_db, ["instagram", "linkedin"])  # OK

    def test_starter_blocks_three_platforms(self):
        u = _make_user("starter")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        with pytest.raises(PlanLimitExceeded) as exc_info:
            assert_platform_count(u_db, ["instagram", "linkedin", "twitter"])
        assert exc_info.value.limit_key == "platforms_allowed"
        assert exc_info.value.current == 3
        assert exc_info.value.max_allowed == 2

    def test_growth_allows_five_platforms(self):
        u = _make_user("growth")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        assert_platform_count(u_db, ["instagram", "linkedin", "twitter", "tiktok", "facebook"])  # OK

    def test_growth_blocks_six_platforms(self):
        u = _make_user("growth")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        with pytest.raises(PlanLimitExceeded):
            assert_platform_count(u_db, ["instagram", "linkedin", "twitter", "tiktok", "facebook", "pinterest"])

    def test_pro_allows_ten_platforms(self):
        u = _make_user("pro")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        assert_platform_count(u_db, ["instagram", "linkedin", "twitter", "tiktok", "facebook",
                                     "pinterest", "youtube", "snapchat", "reddit", "threads"])  # 10 OK

    def test_scale_allows_fifteen_platforms(self):
        u = _make_user("scale")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        platforms = [f"platform_{i}" for i in range(15)]
        assert_platform_count(u_db, platforms)  # OK

    def test_deduplicates_platforms(self):
        """Duplicate platforms should count as one."""
        u = _make_user("starter")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        assert_platform_count(u_db, ["instagram", "instagram"])  # deduped = 1, OK


# ---------------------------------------------------------------------------
# assert_feature()
# ---------------------------------------------------------------------------

class TestAssertFeature:
    def test_starter_has_analytics_ai(self):
        u = _make_user("starter")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        assert_feature(u_db, "analytics_ai")  # must not raise

    def test_growth_has_analytics_ai(self):
        u = _make_user("growth")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        assert_feature(u_db, "analytics_ai")  # must not raise

    def test_starter_lacks_comment_automations(self):
        u = _make_user("starter")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        with pytest.raises(PlanLimitExceeded):
            assert_feature(u_db, "comment_automations")

    def test_growth_lacks_comment_automations(self):
        u = _make_user("growth")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        with pytest.raises(PlanLimitExceeded):
            assert_feature(u_db, "comment_automations")

    def test_pro_has_comment_automations(self):
        u = _make_user("pro")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        assert_feature(u_db, "comment_automations")  # must not raise

    def test_scale_has_comment_automations(self):
        u = _make_user("scale")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        assert_feature(u_db, "comment_automations")  # must not raise

    def test_starter_has_brand_kit(self):
        u = _make_user("starter")
        with Session(engine) as s:
            u_db = s.get(User, u.id)
        assert_feature(u_db, "custom_brand_kit")  # must not raise


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
            current=1,
            max_allowed=1,
            next_tier=PLAN_GROWTH,
        )
        resp = err.to_response()
        assert resp["upgrade_to"] == "growth"
        assert resp["upgrade_price_usd"] == 79

    def test_scale_next_tier_price(self):
        err = PlanLimitExceeded(
            "Need more campaigns",
            limit_key="campaigns_per_month",
            current=10,
            max_allowed=10,
            next_tier=PLAN_SCALE,
        )
        resp = err.to_response()
        assert resp["upgrade_to"] == "scale"
        assert resp["upgrade_price_usd"] == 399
