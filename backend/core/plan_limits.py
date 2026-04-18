"""S1-03: Plan tier definitions and enforcement helpers.

Tier matrix (updated 2026-04):
  starter  — 1 campaign/mo,  10 posts/campaign, 2 platforms,  1 seat   ($29/mo)
  growth   — 5 campaigns/mo, 30 posts/campaign, 5 platforms,  1 seat   ($79/mo)
  pro      — 10 campaigns/mo,60 posts/campaign, 10 platforms, 3 seats  ($259/mo)
  scale    — 20 campaigns/mo,120 posts/campaign,15 platforms, 5 seats  ($399/mo)
  agency   — same as scale (provisioned manually via sales)

These values are checked at the API layer before generation starts so users
see a clear, actionable error rather than a mid-workflow failure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Optional


UNLIMITED = 999_999  # sentinel for "effectively unlimited"


@dataclass(frozen=True)
class PlanLimits:
    plan: str
    campaigns_per_month: int
    posts_per_campaign: int
    platforms_allowed: int           # max simultaneous publish platforms
    team_seats: int                  # max members including owner
    videos_per_month: int            # AI video generations per month (display only)
    analytics_ai: bool               # AI analytics insights enabled
    comment_automations: bool        # comment-to-DM automation enabled
    custom_brand_kit: bool           # brand kit upload/AI generation enabled
    stripe_price_id_monthly: str     # Stripe Price ID (set via env or hardcoded)
    monthly_price_usd: Optional[int] # display price in USD (None = contact sales)


# ---------------------------------------------------------------------------
# Tier definitions — Stripe Price IDs read from env at runtime
# ---------------------------------------------------------------------------

import os as _os

def _price(env_key: str, fallback: str = "") -> str:
    return _os.getenv(env_key, fallback).strip()


PLAN_STARTER = PlanLimits(
    plan="starter",
    campaigns_per_month=1,
    posts_per_campaign=10,
    platforms_allowed=2,
    team_seats=1,
    videos_per_month=1,
    analytics_ai=True,
    comment_automations=False,
    custom_brand_kit=True,
    stripe_price_id_monthly=_price("STRIPE_PRICE_STARTER", "price_starter_placeholder"),
    monthly_price_usd=29,
)

PLAN_GROWTH = PlanLimits(
    plan="growth",
    campaigns_per_month=5,
    posts_per_campaign=30,
    platforms_allowed=5,
    team_seats=1,
    videos_per_month=5,
    analytics_ai=True,
    comment_automations=False,
    custom_brand_kit=True,
    stripe_price_id_monthly=_price("STRIPE_PRICE_GROWTH", "price_growth_placeholder"),
    monthly_price_usd=79,
)

PLAN_PRO = PlanLimits(
    plan="pro",
    campaigns_per_month=10,
    posts_per_campaign=60,
    platforms_allowed=10,
    team_seats=3,
    videos_per_month=10,
    analytics_ai=True,
    comment_automations=True,
    custom_brand_kit=True,
    stripe_price_id_monthly=_price("STRIPE_PRICE_PRO", "price_pro_placeholder"),
    monthly_price_usd=259,
)

PLAN_SCALE = PlanLimits(
    plan="scale",
    campaigns_per_month=20,
    posts_per_campaign=120,
    platforms_allowed=15,
    team_seats=5,
    videos_per_month=15,
    analytics_ai=True,
    comment_automations=True,
    custom_brand_kit=True,
    stripe_price_id_monthly=_price("STRIPE_PRICE_SCALE", "price_scale_placeholder"),
    monthly_price_usd=399,
)

PLAN_AGENCY = PlanLimits(
    plan="agency",
    campaigns_per_month=UNLIMITED,
    posts_per_campaign=UNLIMITED,
    platforms_allowed=UNLIMITED,
    team_seats=UNLIMITED,
    videos_per_month=UNLIMITED,
    analytics_ai=True,
    comment_automations=True,
    custom_brand_kit=True,
    stripe_price_id_monthly="",   # manual provisioning via sales
    monthly_price_usd=None,
)

_TIERS: dict[str, PlanLimits] = {
    "starter": PLAN_STARTER,
    "growth": PLAN_GROWTH,
    "pro": PLAN_PRO,
    "scale": PLAN_SCALE,
    "agency": PLAN_AGENCY,
}

# Plans ordered from lowest to highest (for upgrade CTA logic)
PLAN_ORDER = ["starter", "growth", "pro", "scale", "agency"]


def get_limits(plan: str) -> PlanLimits:
    """Return PlanLimits for the given plan slug. Falls back to starter."""
    return _TIERS.get((plan or "starter").lower().strip(), PLAN_STARTER)


def is_plan_at_least(user_plan: str, required: str) -> bool:
    """True if user_plan >= required in the tier hierarchy."""
    try:
        return PLAN_ORDER.index(user_plan) >= PLAN_ORDER.index(required)
    except ValueError:
        return False


def next_plan_up(user_plan: str) -> Optional[PlanLimits]:
    """Return the next tier above the user's current plan, or None if already at top."""
    try:
        idx = PLAN_ORDER.index(user_plan)
    except ValueError:
        idx = 0
    if idx + 1 >= len(PLAN_ORDER):
        return None
    return _TIERS.get(PLAN_ORDER[idx + 1])


# ---------------------------------------------------------------------------
# Campaign-count enforcement helpers
# ---------------------------------------------------------------------------

from datetime import datetime, timezone as _tz
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlmodel import Session
    from backend.models import User


def count_campaigns_this_month(session: "Session", user_id: int) -> int:
    """Count campaigns created by this user in the current UTC calendar month."""
    from sqlmodel import select, func
    from backend.models import Campaign

    now = datetime.now(_tz.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    stmt = (
        select(func.count(Campaign.id))
        .where(Campaign.user_id == user_id)
        .where(Campaign.created_at >= month_start)
    )
    return session.exec(stmt).one() or 0


def _unlimited_campaigns_email_allowlist() -> frozenset[str]:
    """Comma-separated emails that skip the monthly campaign cap (QA / ops)."""
    raw = _os.getenv("BROKERAI_UNLIMITED_CAMPAIGNS_EMAILS", "").strip().lower()
    if not raw:
        return frozenset()
    return frozenset(e.strip() for e in raw.split(",") if e.strip())


def assert_can_create_campaign(session: "Session", user: "User") -> None:
    """Raise PlanLimitExceeded if the user has hit their monthly campaign cap."""
    limits = get_limits(user.plan)
    if limits.campaigns_per_month >= UNLIMITED:
        return
    email = (getattr(user, "email", None) or "").strip().lower()
    if email and email in _unlimited_campaigns_email_allowlist():
        return
    count = count_campaigns_this_month(session, int(user.id))
    if count >= limits.campaigns_per_month:
        raise PlanLimitExceeded(
            f"You've used {count}/{limits.campaigns_per_month} campaigns "
            f"this month on the {user.plan.title()} plan. "
            "Upgrade to create more.",
            limit_key="campaigns_per_month",
            current=count,
            max_allowed=limits.campaigns_per_month,
            next_tier=next_plan_up(user.plan),
        )


def assert_platform_count(user: "User", platforms: list[str]) -> None:
    """Raise PlanLimitExceeded if the number of selected platforms exceeds the plan limit."""
    limits = get_limits(user.plan)
    if limits.platforms_allowed >= UNLIMITED:
        return
    n = len(set(platforms))
    if n > limits.platforms_allowed:
        raise PlanLimitExceeded(
            f"Your {user.plan.title()} plan allows up to {limits.platforms_allowed} platform(s). "
            f"You selected {n}. Upgrade to publish to more platforms.",
            limit_key="platforms_allowed",
            current=n,
            max_allowed=limits.platforms_allowed,
            next_tier=next_plan_up(user.plan),
        )


def assert_feature(user: "User", feature: str) -> None:
    """Raise PlanLimitExceeded if a boolean feature is not available on the user's plan.

    feature: 'analytics_ai' | 'comment_automations' | 'custom_brand_kit'
    """
    limits = get_limits(user.plan)
    if not getattr(limits, feature, False):
        display = feature.replace("_", " ").title()
        raise PlanLimitExceeded(
            f"{display} is not available on the {user.plan.title()} plan. Upgrade to unlock.",
            limit_key=feature,
            current=0,
            max_allowed=0,
            next_tier=next_plan_up(user.plan),
        )


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class PlanLimitExceeded(Exception):
    """Raised when a user tries to exceed their plan quota."""

    def __init__(
        self,
        message: str,
        *,
        limit_key: str,
        current: int,
        max_allowed: int,
        next_tier: Optional[PlanLimits] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.limit_key = limit_key
        self.current = current
        self.max_allowed = max_allowed
        self.next_tier = next_tier

    def to_response(self) -> dict:
        d: dict = {
            "detail": self.message,
            "limit_key": self.limit_key,
            "current": self.current,
            "max_allowed": self.max_allowed,
            "upgrade_required": True,
        }
        if self.next_tier:
            d["upgrade_to"] = self.next_tier.plan
            d["upgrade_price_usd"] = self.next_tier.monthly_price_usd
        return d


def warn_placeholder_stripe_prices() -> None:
    """Log a warning if any paid plan is using a placeholder Stripe price ID."""
    import logging as _log
    _logger = _log.getLogger("brokerai.plan_limits")
    for tier in [PLAN_STARTER, PLAN_GROWTH, PLAN_PRO, PLAN_SCALE]:
        if "placeholder" in (tier.stripe_price_id_monthly or "").lower():
            _logger.warning(
                "Plan '%s' has a placeholder Stripe price ID ('%s'). "
                "Set STRIPE_PRICE_%s env var before enabling billing.",
                tier.plan,
                tier.stripe_price_id_monthly,
                tier.plan.upper(),
            )
