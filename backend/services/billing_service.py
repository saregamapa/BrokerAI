"""S1-02: Stripe billing — Checkout sessions, Customer Portal, webhook handler.

Environment variables required:
  STRIPE_SECRET_KEY          — sk_live_... or sk_test_...
  STRIPE_WEBHOOK_SECRET      — whsec_... (from Stripe Dashboard or stripe CLI)
  STRIPE_PRICE_STARTER       — price_xxx (monthly Starter price ID)
  STRIPE_PRICE_GROWTH        — price_xxx
  STRIPE_PRICE_PRO           — price_xxx
  APP_URL                    — https://brokerai.app (used for success/cancel URLs)

Supported webhook events:
  checkout.session.completed          — subscription created
  customer.subscription.updated       — plan changed / renewed
  customer.subscription.deleted       — cancelled / lapsed
  invoice.payment_failed              — payment failure (flag in DB)
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlmodel import Session, select

from backend.core.logger import get_logger, log_event
from backend.core.plan_limits import PLAN_ORDER, get_limits

log = get_logger("brokerai.billing")

# ---------------------------------------------------------------------------
# Lazy stripe import — so the app boots even without stripe installed
# ---------------------------------------------------------------------------

def _stripe():
    try:
        import stripe as _s
        key = os.getenv("STRIPE_SECRET_KEY", "").strip()
        if key:
            _s.api_key = key
        return _s
    except ImportError as e:
        raise RuntimeError(
            "stripe package is not installed. Run: pip install stripe>=8.0.0"
        ) from e


# ---------------------------------------------------------------------------
# Price ID → plan slug reverse mapping (built at runtime from env)
# ---------------------------------------------------------------------------

def _price_id_to_plan() -> Dict[str, str]:
    return {
        v: k
        for k, v in {
            "starter": os.getenv("STRIPE_PRICE_STARTER", "").strip(),
            "growth": os.getenv("STRIPE_PRICE_GROWTH", "").strip(),
            "pro": os.getenv("STRIPE_PRICE_PRO", "").strip(),
        }.items()
        if v
    }


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------

def create_checkout_session(
    user_id: int,
    user_email: str,
    plan: str,
    stripe_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a Stripe Checkout session for the given plan.

    Returns { url: str, session_id: str }.
    Raises RuntimeError / stripe.error.StripeError on failure.
    """
    stripe = _stripe()
    limits = get_limits(plan)
    if not limits.stripe_price_id_monthly or limits.stripe_price_id_monthly.endswith("_placeholder"):
        raise ValueError(
            f"STRIPE_PRICE_{plan.upper()} env var is not set. "
            "Add it to your .env file before enabling billing."
        )

    app_url = os.getenv("APP_URL", "https://brokerai.app").rstrip("/")
    params: Dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": limits.stripe_price_id_monthly, "quantity": 1}],
        "success_url": f"{app_url}/billing-success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{app_url}/dashboard.html?billing=cancelled",
        "client_reference_id": str(user_id),
        "customer_email": user_email if not stripe_customer_id else None,
        "metadata": {"user_id": str(user_id), "plan": plan},
        "subscription_data": {
            "metadata": {"user_id": str(user_id), "plan": plan},
        },
        "allow_promotion_codes": True,
    }
    if stripe_customer_id:
        params["customer"] = stripe_customer_id
        params.pop("customer_email", None)

    session = stripe.checkout.Session.create(**params)
    log_event(
        "billing.checkout_created",
        user_id=user_id,
        plan=plan,
        session_id=session.id,
    )
    return {"url": session.url, "session_id": session.id}


# ---------------------------------------------------------------------------
# Customer Portal
# ---------------------------------------------------------------------------

def create_portal_session(
    stripe_customer_id: str,
    return_url: Optional[str] = None,
) -> str:
    """Create a Stripe Customer Portal session. Returns the portal URL."""
    stripe = _stripe()
    app_url = os.getenv("APP_URL", "https://brokerai.app").rstrip("/")
    session = stripe.billing_portal.Session.create(
        customer=stripe_customer_id,
        return_url=return_url or f"{app_url}/dashboard.html",
    )
    log.info("billing.portal_created customer_id=%s", stripe_customer_id[:8])
    return session.url


# ---------------------------------------------------------------------------
# Webhook handler
# ---------------------------------------------------------------------------

def handle_stripe_webhook(
    payload: bytes,
    sig_header: str,
    session: Session,
) -> Dict[str, str]:
    """Verify signature and dispatch webhook event to the correct handler.

    Returns { "status": "ok", "event": event_type } or raises on error.
    """
    stripe = _stripe()
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    if not webhook_secret:
        raise ValueError("STRIPE_WEBHOOK_SECRET is not configured")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except stripe.error.SignatureVerificationError as e:
        log.warning("stripe_webhook_invalid_signature: %s", e)
        raise

    et = event["type"]
    data = event["data"]["object"]
    log.info("stripe_webhook event=%s id=%s", et, event.get("id"))

    if et == "checkout.session.completed":
        _handle_checkout_completed(data, session)
    elif et in ("customer.subscription.updated", "customer.subscription.created"):
        _handle_subscription_updated(data, session)
    elif et == "customer.subscription.deleted":
        _handle_subscription_deleted(data, session)
    elif et == "invoice.payment_failed":
        _handle_payment_failed(data, session)
    else:
        log.debug("stripe_webhook unhandled event=%s", et)

    return {"status": "ok", "event": et}


# ---------------------------------------------------------------------------
# Internal webhook dispatchers
# ---------------------------------------------------------------------------

def _resolve_user_from_stripe(
    data: Any, session: Session
):
    """Attempt to find the User row from Stripe metadata or customer id."""
    from backend.models import User  # local to avoid circular import

    # Prefer metadata user_id (set on checkout + subscription)
    meta = {}
    if hasattr(data, "get"):
        meta = data.get("metadata") or {}
    user_id_str = str(meta.get("user_id") or "").strip()
    if user_id_str.isdigit():
        user = session.get(User, int(user_id_str))
        if user:
            return user

    # Fallback: look up by stripe_customer_id
    customer = ""
    if hasattr(data, "get"):
        customer = str(data.get("customer") or "").strip()
    if customer:
        stmt = select(User).where(User.stripe_customer_id == customer)
        user = session.exec(stmt).first()
        if user:
            return user

    log.warning("stripe_webhook: could not resolve user meta=%s customer=%s", meta, customer)
    return None


def _plan_slug_from_subscription(sub: Any) -> str:
    """Extract plan slug from the first subscription item's price ID."""
    mapping = _price_id_to_plan()
    try:
        items = sub.get("items", {}).get("data", [])
        for item in items:
            price_id = item.get("price", {}).get("id", "")
            if price_id in mapping:
                return mapping[price_id]
    except Exception:
        pass
    meta = sub.get("metadata") or {}
    plan = str(meta.get("plan") or "").strip().lower()
    if plan in PLAN_ORDER:
        return plan
    return "free"


def _handle_checkout_completed(data: Any, session: Session) -> None:
    from backend.models import User

    user = _resolve_user_from_stripe(data, session)
    if user is None:
        return

    customer_id = str(data.get("customer") or "").strip()
    sub_id = str(data.get("subscription") or "").strip()
    meta = data.get("metadata") or {}
    plan = str(meta.get("plan") or "").strip().lower()
    if plan not in PLAN_ORDER:
        plan = "starter"  # safe default

    if customer_id and not user.stripe_customer_id:
        user.stripe_customer_id = customer_id
    if sub_id:
        user.stripe_subscription_id = sub_id
    user.plan = plan
    user.plan_expires_at = None  # Stripe manages renewal
    session.add(user)
    session.commit()
    log_event("billing.plan_activated", user_id=user.id, plan=plan)


def _handle_subscription_updated(data: Any, session: Session) -> None:
    from backend.models import User

    user = _resolve_user_from_stripe(data, session)
    if user is None:
        return

    status = str(data.get("status") or "").strip()
    plan = _plan_slug_from_subscription(data)

    # Update subscription ID in case it changed
    sub_id = str(data.get("id") or "").strip()
    if sub_id:
        user.stripe_subscription_id = sub_id

    if status in ("active", "trialing"):
        user.plan = plan
        user.plan_expires_at = None
        log_event("billing.plan_updated", user_id=user.id, plan=plan, status=status)
    elif status in ("past_due", "unpaid"):
        # Grace period: keep plan but flag expiry
        period_end = data.get("current_period_end")
        if period_end:
            user.plan_expires_at = datetime.fromtimestamp(period_end, tz=timezone.utc).replace(tzinfo=None)
        log_event("billing.payment_overdue", user_id=user.id, plan=plan, status=status)
    elif status in ("canceled", "incomplete_expired"):
        user.plan = "free"
        user.stripe_subscription_id = None
        user.plan_expires_at = None
        log_event("billing.plan_cancelled", user_id=user.id)

    session.add(user)
    session.commit()


def _handle_subscription_deleted(data: Any, session: Session) -> None:
    from backend.models import User

    user = _resolve_user_from_stripe(data, session)
    if user is None:
        return

    user.plan = "free"
    user.stripe_subscription_id = None
    user.plan_expires_at = None
    session.add(user)
    session.commit()
    log_event("billing.subscription_deleted", user_id=user.id)


def _handle_payment_failed(data: Any, session: Session) -> None:
    # Optionally: send email, set a flag, trigger re-engagement flow
    customer = str(data.get("customer") or "").strip()
    amount = data.get("amount_due", 0)
    log_event("billing.payment_failed", customer_id=customer, amount_cents=amount)
    # TODO Sprint 3: trigger Resend payment-failure email


# ---------------------------------------------------------------------------
# Utility: plan name display helpers for frontend
# ---------------------------------------------------------------------------

def plan_display_name(plan: str) -> str:
    return {
        "free": "Free",
        "starter": "Starter",
        "growth": "Growth",
        "pro": "Pro",
        "agency": "Agency",
    }.get(plan, plan.title())


def get_all_plan_summaries() -> list[dict]:
    """Return frontend-consumable pricing data for all non-agency plans."""
    from backend.core.plan_limits import PLAN_STARTER, PLAN_GROWTH, PLAN_PRO

    def _fmt_campaigns(n: int) -> str:
        return "Unlimited" if n >= 999_999 else str(n)

    def _fmt_seats(n: int) -> str:
        return "Unlimited" if n >= 999_999 else str(n)

    return [
        {
            "plan": "starter",
            "name": "Starter",
            "price_usd": 29,
            "price_period": "/month",
            "tagline": "Perfect for solo agents",
            "campaigns_per_month": _fmt_campaigns(PLAN_STARTER.campaigns_per_month),
            "posts_per_campaign": _fmt_campaigns(PLAN_STARTER.posts_per_campaign),
            "platforms": PLAN_STARTER.platforms_allowed,
            "team_seats": _fmt_seats(PLAN_STARTER.team_seats),
            "analytics_ai": PLAN_STARTER.analytics_ai,
            "comment_automations": PLAN_STARTER.comment_automations,
            "highlighted": False,
        },
        {
            "plan": "growth",
            "name": "Growth",
            "price_usd": 79,
            "price_period": "/month",
            "tagline": "For growing teams",
            "campaigns_per_month": _fmt_campaigns(PLAN_GROWTH.campaigns_per_month),
            "posts_per_campaign": _fmt_campaigns(PLAN_GROWTH.posts_per_campaign),
            "platforms": PLAN_GROWTH.platforms_allowed,
            "team_seats": _fmt_seats(PLAN_GROWTH.team_seats),
            "analytics_ai": PLAN_GROWTH.analytics_ai,
            "comment_automations": PLAN_GROWTH.comment_automations,
            "highlighted": True,   # most popular
        },
        {
            "plan": "pro",
            "name": "Pro",
            "price_usd": 199,
            "price_period": "/month",
            "tagline": "For power users",
            "campaigns_per_month": _fmt_campaigns(PLAN_PRO.campaigns_per_month),
            "posts_per_campaign": _fmt_campaigns(PLAN_PRO.posts_per_campaign),
            "platforms": PLAN_PRO.platforms_allowed,
            "team_seats": _fmt_seats(PLAN_PRO.team_seats),
            "analytics_ai": PLAN_PRO.analytics_ai,
            "comment_automations": PLAN_PRO.comment_automations,
            "highlighted": False,
        },
    ]
