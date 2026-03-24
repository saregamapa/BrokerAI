from datetime import datetime
from typing import List, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    password_hash: str
    timezone: str = "America/New_York"  # IANA timezone, default Eastern
    plan: str = "free"  # free | pro | agency (SaaS readiness placeholder)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Campaign(SQLModel, table=True):
    __tablename__ = "campaigns"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    # draft → pending_approval (after graph phase 1) → approved → publishing → completed
    status: str = "draft"
    graph_thread_id: str = ""
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
    hashtags: str = "[]"  # JSON array string
    image_url: str = ""
    video_script: str = ""
    day_label: Optional[str] = None
    publish_platforms: List[str] = Field(
        default_factory=lambda: ["facebook"],
        sa_column=Column(JSON),
    )
    # pending_approval | approved | published | publish_failed | pending (legacy)
    status: str = "pending"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    scheduled_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    platform_response: str = "{}"
    compliance_passed: Optional[bool] = None
    compliance_checked_at: Optional[datetime] = None
    compliance_issues: str = "[]"  # JSON array string
    publish_attempts: int = 0
    idempotency_key: Optional[str] = None  # Prevents double publishing
    # Aggregated performance (from Ayrshare analytics or placeholders)
    likes: int = 0
    comments: int = 0
    shares: int = 0
    impressions: int = 0
    engagement_rate: float = 0.0  # 0–100, (likes+comments+shares)/max(impressions,1)
