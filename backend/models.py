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
    plan: str = "free"  # free | pro | agency (SaaS readiness placeholder)
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
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # RBAC fields
    # account_type: "individual" | "team" | "org"
    account_type: str = Field(default="individual")
    # role: "owner" | "admin" | "member"
    role: str = Field(default="owner")
    # FK to teams.id — null for individual accounts
    team_id: Optional[int] = Field(default=None, foreign_key="teams.id", index=True)


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
    # Optional lead capture form attached to this campaign
    lead_form_id: Optional[int] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


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



class CampaignTemplate(SQLModel, table=True):
    """User-saved reusable campaign blueprint (wizard payload snapshot)."""
    __tablename__ = "campaign_templates"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    team_id: Optional[int] = Field(default=None, foreign_key="teams.id", index=True)
    name: str = Field(default="Untitled Template", sa_column=Column(Text))
    description: str = Field(default="", sa_column=Column(Text))
    # JSON-serialized wizard payload (goal, objective, audience, platforms, tone, content_type, etc.)
    payload: str = Field(default="{}", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class LeadForm(SQLModel, table=True):
    """User-owned lead capture form. Fields are a JSON array of
    {key, label, type, required, options}."""
    __tablename__ = "lead_forms"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    team_id: Optional[int] = Field(default=None, foreign_key="teams.id", index=True)
    name: str = Field(default="Untitled Lead Form", sa_column=Column(Text))
    headline: str = Field(default="", sa_column=Column(Text))
    description: str = Field(default="", sa_column=Column(Text))
    # JSON array string: [{"key":"email","label":"Email","type":"email","required":true}, ...]
    fields: str = Field(default="[]", sa_column=Column(Text))
    thank_you_message: str = Field(
        default="Thanks! We'll be in touch soon.",
        sa_column=Column(Text),
    )
    redirect_url: str = Field(default="", sa_column=Column(Text))
    public_slug: str = Field(default="", index=True)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Lead(SQLModel, table=True):
    """A single lead capture row."""
    __tablename__ = "leads"

    id: Optional[int] = Field(default=None, primary_key=True)
    form_id: int = Field(foreign_key="lead_forms.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    # JSON blob of submitted values keyed by field.key
    data: str = Field(default="{}", sa_column=Column(Text))
    source: str = Field(default="", sa_column=Column(Text))  # e.g. "campaign:42" or "post:123"
    utm_campaign: str = Field(default="", sa_column=Column(Text))
    utm_source: str = Field(default="", sa_column=Column(Text))
    ip: str = Field(default="")
    user_agent: str = Field(default="", sa_column=Column(Text))
    captured_at: datetime = Field(default_factory=datetime.utcnow)


class CommentAutomation(SQLModel, table=True):
    """Keyword-triggered comment-to-DM automation attached to a post (or catch-all).

    When a matching comment is received on the target post, we (a) optionally
    post a public reply to the comment and (b) send a DM to the commenter
    containing a call-to-action — usually a lead-form link.
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
    # Optional associated lead form (we attach its public slug into {link})
    lead_form_id: Optional[int] = Field(default=None, foreign_key="lead_forms.id", index=True)
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


class CanvaDesign(SQLModel, table=True):
    """A Canva design generated for (or imported into) a post.

    We don't proxy Canva's API from the server unless CANVA_API_TOKEN is set —
    instead we store design metadata (edit_url, export_url) and let the
    frontend link out to Canva for editing. Once the user exports, the
    export_url is attached back to the Post as image/slide media.
    """
    __tablename__ = "canva_designs"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    post_id: Optional[int] = Field(default=None, foreign_key="posts.id", index=True)
    campaign_id: Optional[int] = Field(default=None, foreign_key="campaigns.id", index=True)

    # Canva identifiers
    external_id: str = Field(default="", index=True)  # Canva design ID
    template_id: str = Field(default="")

    title: str = Field(default="", sa_column=Column(Text))
    design_type: str = Field(default="instagram-post")  # presentation | instagram-post | facebook-post | etc

    edit_url: str = Field(default="", sa_column=Column(Text))      # Canva editor deep-link
    share_url: str = Field(default="", sa_column=Column(Text))     # public view
    thumbnail_url: str = Field(default="", sa_column=Column(Text))
    export_url: str = Field(default="", sa_column=Column(Text))    # hosted PNG/JPG/MP4 url
    export_format: str = Field(default="png")

    # AI prompt + element data used to generate/fill the design (JSON)
    prompt: str = Field(default="", sa_column=Column(Text))
    autofill_data: str = Field(default="{}", sa_column=Column(Text))  # JSON

    # pending | ready | failed | imported
    status: str = Field(default="pending")
    error: str = Field(default="", sa_column=Column(Text))

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
