from datetime import datetime
from typing import List, Optional

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel


class Team(SQLModel, table=True):
    __tablename__ = "teams"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(default="", sa_column=Column(Text))
    # "team" (small business, max 5) or "org" (organisation, max 13)
    account_type: str = Field(default="team")
    owner_id: int = Field(foreign_key="users.id", index=True)
    max_members: int = Field(default=5)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TeamInvite(SQLModel, table=True):
    __tablename__ = "team_invites"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True)          # invite recipient
    team_id: int = Field(foreign_key="teams.id", index=True)
    role: str = Field(default="member")     # always "member" for invite flow
    token: str = Field(unique=True, index=True)
    is_used: bool = Field(default=False)
    expires_at: datetime = Field()
    created_at: datetime = Field(default_factory=datetime.utcnow)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    password_hash: str
    timezone: str = "UTC"  # IANA timezone; stored for display + scheduling context
    plan: str = "starter"  # starter | growth | pro | scale | agency
    # S1-02: Stripe billing
    stripe_customer_id: Optional[str] = Field(default=None, index=True)
    stripe_subscription_id: Optional[str] = Field(default=None)
    plan_expires_at: Optional[datetime] = Field(default=None)   # null = never / managed by Stripe
    # Ayrshare Business: per-user profile for SSO linking + publishing (Profile-Key header)
    ayrshare_profile_key: Optional[str] = Field(default=None)
    social_connected: bool = Field(default=False)
    # Optional profile/page URLs for AI personalization (set on Connect Accounts)
    facebook_url: str = ""
    instagram_url: str = ""
    linkedin_url: str = ""
    # Brand kit (wizard "Media & Design" step — uploaded OR AI-generated)
    brand_logo_url: str = ""
    brand_primary_color: str = ""      # e.g. "#0F62FE"
    brand_secondary_color: str = ""    # e.g. "#111827"
    brand_font: str = ""                # e.g. "Inter"
    brand_voice: str = ""               # tone descriptor ("professional", "playful", etc.)
    brand_source: str = ""              # "upload" | "ai" | ""
    brand_key_messages: Optional[str] = Field(default=None)
    brand_forbidden_words: Optional[str] = Field(default=None)
    brand_cta_style: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # RBAC fields
    # account_type: "individual" | "team" | "org"
    account_type: str = Field(default="individual")
    # role: "owner" | "admin" | "member"
    role: str = Field(default="owner")
    # FK to teams.id — null for individual accounts
    team_id: Optional[int] = Field(default=None, foreign_key="teams.id", index=True)
    # S0-06: Email verification
    email_verified: bool = Field(default=False)
    # S0-07: Account lockout after repeated failed logins
    failed_login_attempts: int = Field(default=0)
    locked_until: Optional[datetime] = Field(default=None)
    # S4-05: Google OAuth2 — sub is Google's unique user identifier
    google_id: Optional[str] = Field(default=None, index=True)
    display_name: Optional[str] = Field(default=None)
    avatar_url: Optional[str] = Field(default=None)


class SocialAccount(SQLModel, table=True):
    __tablename__ = "social_accounts"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    # Single source of truth row can use platform="ayrshare_profile"
    platform: str = Field(default="ayrshare_profile", index=True)
    is_connected: bool = Field(default=False)
    profile_key: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Campaign(SQLModel, table=True):
    __tablename__ = "campaigns"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    # RBAC: team campaigns are visible to all team members; nullable for individual
    team_id: Optional[int] = Field(default=None, foreign_key="teams.id", index=True)
    # creator and approver tracking
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    approved_by: Optional[int] = Field(default=None, foreign_key="users.id")
    # draft → in_review → approved → publishing → published | failed
    # Legacy: "pending_approval" maps to "in_review" on read
    status: str = "draft"
    graph_thread_id: str = ""
    # Campaign metadata (from wizard inputs)
    name: str = Field(default="", sa_column=Column(Text))
    objective: str = Field(default="", sa_column=Column(Text))
    target_audience: str = Field(default="", sa_column=Column(Text))
    facebook_url: str = ""
    instagram_url: str = ""
    linkedin_url: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    # S5-09: soft-delete — null means active; set to UTC timestamp when deleted
    deleted_at: Optional[datetime] = Field(default=None, index=True)


class Post(SQLModel, table=True):
    __tablename__ = "posts"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="users.id", index=True)
    campaign_id: Optional[int] = Field(default=None, foreign_key="campaigns.id", index=True)
    caption: str
    # Analytics / intelligence (primary network + provider id + copy snapshot)
    platform: str = Field(default="")  # e.g. linkedin, twitter, instagram, facebook
    social_post_id: str = Field(default="")  # Ayrshare post id for /analytics/post
    content: str = Field(default="", sa_column=Column(Text))
    hashtags: str = "[]"  # JSON array string
    image_url: str = ""
    # Carousel slides: JSON array of {image_url, caption_overlay, alt_text, order}
    slides: str = Field(default="[]", sa_column=Column(Text))
    is_carousel: bool = Field(default=False)
    video_script: str = ""
    # Denormalized playable URL (wizard / Sora serve); kept so review UI survives JSON churn.
    embed_video_url: str = Field(default="", sa_column=Column(Text))
    day_label: Optional[str] = None
    publish_platforms: List[str] = Field(
        default_factory=lambda: ["facebook"],
        sa_column=Column(JSON),
    )
    # draft | review | approved | publishing | published | failed
    status: str = "draft"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    scheduled_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    platform_response: str = "{}"
    compliance_passed: Optional[bool] = None
    compliance_checked_at: Optional[datetime] = None
    compliance_issues: str = "[]"  # JSON array string
    publish_attempts: int = 0
    max_attempts: int = Field(default=3)
    idempotency_key: Optional[str] = Field(default=None, index=True)
    is_locked: bool = Field(default=False)
    lock_timestamp: Optional[datetime] = None
    next_publish_attempt_at: Optional[datetime] = None
    last_error: str = Field(default="", sa_column=Column(Text))
    # Aggregated performance (from Ayrshare analytics or placeholders)
    likes: int = 0
    comments: int = 0
    shares: int = 0
    impressions: int = 0
    engagement_rate: float = 0.0  # 0–100, (likes+comments+shares)/max(impressions,1)
    # S5-09: soft-delete — null means active; set to UTC timestamp when deleted
    deleted_at: Optional[datetime] = Field(default=None, index=True)
    # S5-02: A/B caption testing
    ab_variant_b: Optional[str] = Field(default=None, sa_column=Column(Text))  # alternative caption
    ab_winner: Optional[str] = Field(default=None)  # "a" or "b" or None
    ab_status: Optional[str] = Field(default=None)  # "testing" | "selected" | None


class CommentAutomation(SQLModel, table=True):
    """Keyword-triggered comment-to-DM automation attached to a post (or catch-all).

    When a matching comment is received on the target post, we (a) optionally
    post a public reply to the comment and (b) send a DM to the commenter
    containing a call-to-action link.
    """
    __tablename__ = "comment_automations"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    team_id: Optional[int] = Field(default=None, foreign_key="teams.id", index=True)

    name: str = Field(default="Untitled Automation", sa_column=Column(Text))
    # Target scope: either a specific published post OR any post on a platform
    post_id: Optional[int] = Field(default=None, foreign_key="posts.id", index=True)
    platform: str = Field(default="instagram", index=True)
    # External post id from Ayrshare (for webhook correlation) — optional
    external_post_id: str = Field(default="", index=True)

    # JSON array of lowercase keyword strings
    keywords: str = Field(default="[]", sa_column=Column(Text))
    match_mode: str = Field(default="any")  # any | all | exact
    case_sensitive: bool = Field(default=False)

    reply_comment_enabled: bool = Field(default=True)
    reply_comment_template: str = Field(
        default="Thanks for the interest! Just sent you a DM 📩",
        sa_column=Column(Text),
    )

    dm_enabled: bool = Field(default=True)
    dm_template: str = Field(
        default="Hi {handle}! Here's the info you asked about: {link}",
        sa_column=Column(Text),
    )
    link_url: str = Field(default="", sa_column=Column(Text))

    is_active: bool = Field(default=True)
    trigger_count: int = Field(default=0)
    last_triggered_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CommentTrigger(SQLModel, table=True):
    """Log of every comment that matched (and attempted to fire) an automation."""
    __tablename__ = "comment_triggers"

    id: Optional[int] = Field(default=None, primary_key=True)
    automation_id: int = Field(foreign_key="comment_automations.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)

    commenter_handle: str = Field(default="", sa_column=Column(Text))
    commenter_id: str = Field(default="")
    comment_text: str = Field(default="", sa_column=Column(Text))
    external_comment_id: str = Field(default="", index=True)
    matched_keyword: str = Field(default="")

    reply_sent: bool = Field(default=False)
    reply_error: str = Field(default="", sa_column=Column(Text))
    dm_sent: bool = Field(default=False)
    dm_error: str = Field(default="", sa_column=Column(Text))

    triggered_at: datetime = Field(default_factory=datetime.utcnow)


class BrandAsset(SQLModel, table=True):
    """User-uploaded brand files (logos, templates, guidelines) from the wizard."""

    __tablename__ = "brand_assets"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    # logo | template | document
    kind: str = Field(default="document", index=True)
    original_filename: str = Field(default="", sa_column=Column(Text))
    # Stored basename only (UUID + allowed extension), under uploads/brand/{user_id}/
    stored_filename: str = Field(default="", sa_column=Column(Text))
    content_type: str = Field(default="application/octet-stream")
    size_bytes: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# S0-03: Refresh tokens (opaque, DB-backed, one row per active session)
# ---------------------------------------------------------------------------

class RefreshToken(SQLModel, table=True):
    """Stores SHA-256 hash of opaque refresh tokens. Raw token is never persisted."""

    __tablename__ = "refresh_tokens"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(unique=True, index=True)   # SHA-256 of raw token
    revoked: bool = Field(default=False)
    expires_at: datetime = Field()                     # UTC naive
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# S0-04: Password reset tokens (one-time, 15-min TTL)
# ---------------------------------------------------------------------------

class PasswordResetToken(SQLModel, table=True):
    """Short-lived token issued on forgot-password flow. Used once then deleted."""

    __tablename__ = "password_reset_tokens"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(unique=True, index=True)   # SHA-256 of raw token
    used: bool = Field(default=False)
    expires_at: datetime = Field()                     # UTC naive
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# S0-06: Email verification tokens (24-hr TTL)
# ---------------------------------------------------------------------------

class EmailVerificationToken(SQLModel, table=True):
    """Token emailed to new users to verify their address before first use."""

    __tablename__ = "email_verification_tokens"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(unique=True, index=True)   # SHA-256 of raw token
    used: bool = Field(default=False)
    expires_at: datetime = Field()                     # UTC naive
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# S3-01: In-app notifications
# ---------------------------------------------------------------------------

class Notification(SQLModel, table=True):
    """In-app notifications for key user events."""
    __tablename__ = "notifications"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    # Types: "post_published" | "post_failed" | "campaign_approved" | "invite_accepted" | "approval_needed" | "system"
    type: str = Field(default="system", index=True)
    title: str = Field(default="", sa_column=Column(Text))
    message: str = Field(default="", sa_column=Column(Text))
    # Optional link to the relevant resource
    action_url: str = Field(default="")
    is_read: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # Optional extra JSON payload (e.g. post_id, campaign_id)
    extra: str = Field(default="{}", sa_column=Column(Text))


# ---------------------------------------------------------------------------
# S3-07: Content Library
# ---------------------------------------------------------------------------

class ContentItem(SQLModel, table=True):
    """
    S3-07 — Content Library item.

    Users can save captions, hashtag sets, and image URLs
    from the review page into their personal library, then re-use them
    in the wizard (Step 4 media panel).
    """
    __tablename__ = "content_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    team_id: Optional[int] = Field(default=None, foreign_key="teams.id", index=True)
    # "caption" | "hashtag_set" | "image_url" | "template"
    kind: str = Field(default="caption", index=True)
    # Primary content: caption text, hashtag string, or image URL
    content: str = Field(default="", sa_column=Column(Text))
    # Human-readable label chosen by the user
    label: str = Field(default="", sa_column=Column(Text))
    # Platform the content was originally created for (optional)
    platform: str = Field(default="")
    # JSON array of user-defined tag strings for filtering
    tags: str = Field(default="[]", sa_column=Column(Text))
    # Optional thumbnail URL (for image_url kind)
    thumbnail_url: str = Field(default="")
    # Usage tracking
    use_count: int = Field(default=0)
    last_used_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# S5-08: Audit Log
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# S5-06: Comment Automation Rules (keyword-triggered auto-reply)
# ---------------------------------------------------------------------------

class AutomationRule(SQLModel, table=True):
    """
    S5-06 — keyword-triggered comment auto-reply rule.

    When a social comment contains one of the trigger_keywords, the system
    can post a public reply (reply_template) and/or send a DM (dm_template).
    Supports {{name}} placeholder replacement at runtime.
    """
    __tablename__ = "automations"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    post_id: Optional[int] = Field(default=None, foreign_key="posts.id", index=True)
    campaign_id: Optional[int] = Field(default=None, foreign_key="campaigns.id", index=True)

    # Human-readable label
    name: str = Field(default="", sa_column=Column(Text))
    # Comma-separated trigger words, e.g. "hello,hi,interested"
    trigger_keywords: str = Field(default="", sa_column=Column(Text))
    # Public reply body — supports {{name}} placeholder
    reply_template: str = Field(default="", sa_column=Column(Text))
    # Optional DM body — supports {{name}} placeholder
    dm_template: Optional[str] = Field(default=None, sa_column=Column(Text))

    is_active: bool = Field(default=True)
    # Incremented each time a comment triggers this rule
    match_count: int = Field(default=0)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AuditEvent(SQLModel, table=True):
    """Immutable audit trail for all significant user and system actions."""
    __tablename__ = "audit_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    # Who performed the action (None for system-triggered events)
    actor_user_id: Optional[int] = Field(default=None, foreign_key="users.id", index=True)
    # What happened
    event_type: str = Field(index=True)  # e.g. "campaign.created", "post.approved", "post.published", "campaign.deleted"
    # What entity was affected
    entity_type: str = Field(default="")  # "campaign" | "post" | "user" | "team"
    entity_id: Optional[int] = Field(default=None, index=True)
    # Human-readable summary
    summary: str = Field(default="", sa_column=Column(Text))
    # JSON snapshot of changed fields (before -> after)
    diff: str = Field(default="{}", sa_column=Column(Text))
    # Request context
    ip_address: str = Field(default="")
    user_agent: str = Field(default="")
    request_id: str = Field(default="")
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
