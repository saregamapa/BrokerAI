"""
S3-03 — Transactional email via Resend API.

Set RESEND_API_KEY and FROM_EMAIL in .env to activate.
All functions are best-effort: log failures, never raise.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

from backend.core.logger import get_logger

log = get_logger("brokerai.email")

RESEND_API_URL = "https://api.resend.com/emails"


def _resend_api_key() -> str:
    return os.getenv("RESEND_API_KEY", "").strip()


def _from_email() -> str:
    return os.getenv("FROM_EMAIL", "BrokerAI <noreply@brokerai.io>").strip()


def _send_email(to: str, subject: str, html: str) -> bool:
    """Send a single email via Resend. Returns True on success."""
    api_key = _resend_api_key()
    if not api_key:
        log.debug("email_skipped_no_api_key to=%s subject=%s", to, subject)
        return False
    try:
        resp = httpx.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"from": _from_email(), "to": [to], "subject": subject, "html": html},
            timeout=15.0,
        )
        if resp.status_code in (200, 201):
            log.info("email_sent to=%s subject=%s", to, subject)
            return True
        log.warning("email_failed to=%s status=%s body=%s", to, resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        log.warning("email_exception to=%s err=%s", to, e)
        return False


def _html_wrap(content: str) -> str:
    """Minimal branded HTML wrapper."""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#F9FAFB;font-family:-apple-system,BlinkMacSystemFont,'Inter',sans-serif;">
  <div style="max-width:560px;margin:40px auto;background:white;border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.08);">
    <div style="background:#111827;padding:24px 32px;display:flex;align-items:center;gap:12px;">
      <span style="font-size:22px;font-weight:800;color:#D97706;letter-spacing:-0.5px;">BrokerAI</span>
    </div>
    <div style="padding:32px;">
      {content}
    </div>
    <div style="background:#F9FAFB;padding:16px 32px;text-align:center;font-size:12px;color:#9CA3AF;border-top:1px solid #F3F4F6;">
      &copy; 2026 BrokerAI &middot; <a href="#" style="color:#D97706;text-decoration:none;">Unsubscribe</a>
    </div>
  </div>
</body></html>"""


def send_post_published_email(to: str, platform: str, campaign_name: str, review_url: str = "/review.html") -> bool:
    html = _html_wrap(f"""
      <h2 style="margin:0 0 8px;font-size:20px;color:#111827;">Your post was published! 🎉</h2>
      <p style="color:#6B7280;margin:0 0 20px;line-height:1.6;">Great news — your campaign <strong style="color:#111827;">{campaign_name}</strong> post was successfully published to <strong style="color:#D97706;">{platform.title()}</strong>.</p>
      <a href="{review_url}" style="display:inline-block;background:#D97706;color:white;padding:12px 24px;border-radius:8px;font-weight:600;text-decoration:none;font-size:14px;">View Campaign</a>
      <p style="color:#9CA3AF;font-size:12px;margin-top:24px;">Your analytics will update within a few minutes as engagement comes in.</p>
    """)
    return _send_email(to, "Your BrokerAI post was published ✅", html)


def send_post_failed_email(to: str, platform: str, error_hint: str = "", connect_url: str = "/connect.html") -> bool:
    html = _html_wrap(f"""
      <h2 style="margin:0 0 8px;font-size:20px;color:#111827;">Post failed to publish ⚠️</h2>
      <p style="color:#6B7280;margin:0 0 12px;line-height:1.6;">We were unable to publish your post to <strong style="color:#DC2626;">{platform.title()}</strong> after multiple attempts.</p>
      {f'<p style="background:#FEF2F2;color:#DC2626;padding:10px 14px;border-radius:6px;font-size:13px;margin:0 0 20px;">{error_hint}</p>' if error_hint else ''}
      <a href="{connect_url}" style="display:inline-block;background:#DC2626;color:white;padding:12px 24px;border-radius:8px;font-weight:600;text-decoration:none;font-size:14px;">Reconnect Social Account</a>
    """)
    return _send_email(to, "Action needed: BrokerAI post failed to publish", html)


def send_team_invite_email(to: str, inviter_name: str, team_name: str, invite_link: str) -> bool:
    html = _html_wrap(f"""
      <h2 style="margin:0 0 8px;font-size:20px;color:#111827;">You've been invited to BrokerAI</h2>
      <p style="color:#6B7280;margin:0 0 20px;line-height:1.6;"><strong style="color:#111827;">{inviter_name}</strong> has invited you to join the <strong style="color:#D97706;">{team_name}</strong> team on BrokerAI.</p>
      <a href="{invite_link}" style="display:inline-block;background:#D97706;color:white;padding:12px 24px;border-radius:8px;font-weight:600;text-decoration:none;font-size:14px;">Accept Invitation</a>
      <p style="color:#9CA3AF;font-size:12px;margin-top:24px;">This invite expires in 7 days.</p>
    """)
    return _send_email(to, f"{inviter_name} invited you to join BrokerAI", html)


def send_approval_needed_email(to: str, campaign_name: str, requester_name: str, review_url: str = "/review.html") -> bool:
    html = _html_wrap(f"""
      <h2 style="margin:0 0 8px;font-size:20px;color:#111827;">Campaign approval needed</h2>
      <p style="color:#6B7280;margin:0 0 20px;line-height:1.6;"><strong style="color:#111827;">{requester_name}</strong> has submitted <strong style="color:#D97706;">{campaign_name}</strong> for your approval.</p>
      <a href="{review_url}" style="display:inline-block;background:#111827;color:white;padding:12px 24px;border-radius:8px;font-weight:600;text-decoration:none;font-size:14px;">Review Campaign</a>
    """)
    return _send_email(to, f"Approval needed: {campaign_name}", html)


def send_password_reset_email(to: str, reset_link: str) -> bool:
    html = _html_wrap(f"""
      <h2 style="margin:0 0 8px;font-size:20px;color:#111827;">Reset your password</h2>
      <p style="color:#6B7280;margin:0 0 20px;line-height:1.6;">We received a request to reset your BrokerAI password. Click the button below to set a new password. This link expires in 15 minutes.</p>
      <a href="{reset_link}" style="display:inline-block;background:#D97706;color:white;padding:12px 24px;border-radius:8px;font-weight:600;text-decoration:none;font-size:14px;">Reset Password</a>
      <p style="color:#9CA3AF;font-size:12px;margin-top:24px;">If you didn't request this, you can safely ignore this email. Your password will not change.</p>
    """)
    return _send_email(to, "Reset your BrokerAI password", html)


def send_email_verification_email(to: str, verify_link: str) -> bool:
    html = _html_wrap(f"""
      <h2 style="margin:0 0 8px;font-size:20px;color:#111827;">Verify your email address</h2>
      <p style="color:#6B7280;margin:0 0 20px;line-height:1.6;">Thanks for signing up for BrokerAI! Click the button below to verify your email address.</p>
      <a href="{verify_link}" style="display:inline-block;background:#D97706;color:white;padding:12px 24px;border-radius:8px;font-weight:600;text-decoration:none;font-size:14px;">Verify Email</a>
      <p style="color:#9CA3AF;font-size:12px;margin-top:24px;">This link expires in 24 hours. If you didn't sign up, you can ignore this email.</p>
    """)
    return _send_email(to, "Verify your BrokerAI email address", html)
