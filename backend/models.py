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
