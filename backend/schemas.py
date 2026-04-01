from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from backend.timeutil import is_valid_iana_timezone


class GenerateCampaignRequest(BaseModel):
    business_type: str = "Small Business"
    goal: str
    location: str
    platforms: List[str] = Field(default_factory=list)
    frequency: str = "3 per week"
    # ISO date YYYY-MM-DD for first post week (optional)
    start_date: Optional[str] = None
    audience: Optional[str] = None
    # IANA timezone string (e.g. "America/Los_Angeles")
    timezone: Optional[str] = None
    # Preferred posting hour in user's local time (0-23)
    post_hour: int = 10
    # AI / media toggles (wizard)
    ai_text_enabled: bool = True
    ai_images_enabled: bool = True
    video_scripts_enabled: bool = True

    @field_validator("platforms")
    @classmethod
    def _platforms_not_empty(cls, v: List[str]) -> List[str]:
        cleaned = [str(x).strip() for x in (v or []) if str(x).strip()]
        if not cleaned:
            raise ValueError("Select at least one publish platform")
        return cleaned

    @field_validator("timezone")
    @classmethod
    def _timezone_optional_iana(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if not str(v).strip():
            return None
        s = str(v).strip()
        if not is_valid_iana_timezone(s):
            raise ValueError(f"Invalid IANA timezone: {s!r}")
        return s


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[int] = None
    campaign_id: Optional[int] = None
    day: Optional[str] = None
    caption: str
    hashtags: List[str] = Field(default_factory=list)
    image_url: Optional[str] = ""
    video_script: Optional[str] = ""
    publish_platforms: List[str] = Field(default_factory=list)
    status: str = "pending"
    created_at: Optional[datetime] = None
    scheduled_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    platform_response: Optional[Dict[str, Any]] = None
    compliance_passed: Optional[bool] = None
    compliance_issues: List[str] = Field(default_factory=list)
    last_error: Optional[str] = None
    is_locked: bool = False
    next_publish_attempt_at: Optional[datetime] = None
    platform: str = ""
    post_id: str = ""  # Ayrshare / provider id (maps from social_post_id)
    content: str = ""
    likes: int = 0
    comments: int = 0
    shares: int = 0
    impressions: int = 0
    engagement_rate: float = 0.0


class AnalyticsPostRow(BaseModel):
    """Single row for the performance dashboard table."""

    id: int
    post_id: str = ""
    platform: str = ""
    content: str = ""
    created_at: Optional[datetime] = None
    likes: int = 0
    comments: int = 0
    impressions: int = 0
    engagement_rate: float = 0.0
    status: str = ""
    performance_tier: str = "pending"  # top | low | mid | pending


class AnalyticsSummaryOut(BaseModel):
    avg_engagement_rate: float
    total_impressions: int
    total_likes: int
    total_comments: int
    best_post_id: Optional[int] = None
    worst_post_id: Optional[int] = None
    published_count: int = 0


class PerformanceAnalyticsAIOut(BaseModel):
    insights: List[str] = Field(default_factory=list)
    mistakes: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    next_post_ideas: List[str] = Field(default_factory=list)


class AnalyticsBulkUpdateOut(BaseModel):
    updated: int
    failed: int
    total: int


class InviteOut(BaseModel):
    """Response returned when an owner creates an invite."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    team_id: int
    role: str
    token: str
    is_used: bool
    expires_at: datetime
    created_at: datetime
    # Computed field — constructed by the route, not stored in DB
    signup_url: str = ""


class InviteLookupOut(BaseModel):
    """Response for GET /invite-info?token=XYZ — used by frontend pre-fill."""
    valid: bool
    email: Optional[str] = None
    team_id: Optional[int] = None
    error: Optional[str] = None


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    account_type: str
    owner_id: int
    max_members: int
    created_at: Optional[datetime] = None


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: str
    account_type: str
    team_id: Optional[int] = None


class InviteMemberRequest(BaseModel):
    email: EmailStr


class UpdateRoleRequest(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: str) -> str:
        v = str(v).strip().lower()
        if v not in ("admin", "member"):
            raise ValueError("role must be 'admin' or 'member'")
        return v


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    status: str
    name: str = ""
    objective: str = ""
    target_audience: str = ""
    facebook_url: str = ""
    instagram_url: str = ""
    linkedin_url: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # RBAC
    team_id: Optional[int] = None
    created_by: Optional[int] = None
    approved_by: Optional[int] = None


class CampaignDetailOut(BaseModel):
    campaign: CampaignOut
    posts: List[PostOut]


class GenerateCampaignResponse(BaseModel):
    campaign_id: int
    posts: List[PostOut]


class ApproveCampaignRequest(BaseModel):
    campaign_id: int


class CheckComplianceRequest(BaseModel):
    caption: str
    post_id: Optional[int] = None


class CheckComplianceResponse(BaseModel):
    passed: bool
    issues: List[str] = Field(default_factory=list)
    suggested_fix: str = ""


class UpdatePostRequest(BaseModel):
    caption: Optional[str] = None
    hashtags: Optional[List[str]] = None


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    timezone: Optional[str] = None
    # RBAC: account type chosen at signup (ignored when invite_token is present)
    account_type: str = Field(default="individual")
    # Team/org name (required when account_type != "individual" and no invite_token)
    team_name: Optional[str] = None
    # Invite token — when present, bypasses normal account_type logic
    invite_token: Optional[str] = None

    @field_validator("account_type")
    @classmethod
    def _valid_account_type(cls, v: str) -> str:
        v = str(v).strip().lower()
        if v not in ("individual", "team", "org"):
            raise ValueError("account_type must be 'individual', 'team', or 'org'")
        return v

    @field_validator("timezone")
    @classmethod
    def _signup_timezone_optional(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not str(v).strip():
            return None
        s = str(v).strip()
        if not is_valid_iana_timezone(s):
            return None
        return s


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    social_connected: bool = False
    timezone: str = "UTC"
    facebook_url: str = ""
    instagram_url: str = ""
    linkedin_url: str = ""
    # RBAC
    account_type: str = "individual"
    role: str = "owner"
    team_id: Optional[int] = None


class UpdateProfileUrlsRequest(BaseModel):
    """Optional profile/page URLs for AI context (saved from Connect Accounts)."""

    facebook_url: Optional[str] = None
    instagram_url: Optional[str] = None
    linkedin_url: Optional[str] = None

    @field_validator("facebook_url", "instagram_url", "linkedin_url", mode="before")
    @classmethod
    def _empty_url_to_none(cls, v: object) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        return str(v).strip() or None

    @field_validator("facebook_url", "instagram_url", "linkedin_url")
    @classmethod
    def _basic_http_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("URL must start with http:// or https://")
        if len(v) > 2048:
            raise ValueError("URL is too long (max 2048 characters)")
        return v


class ConnectSocialResponse(BaseModel):
    connect_url: str


class SocialStatusResponse(BaseModel):
    connected: bool
    state: Literal["connected", "not_connected", "verify_failed_temp", "pending_oauth"] = "not_connected"
    profile_key_present: bool = False
    can_create_campaign: bool = False
    last_verified_at: Optional[datetime] = None
    ayrshare_sync_ok: bool = True
    """False if Ayrshare GET /profiles verification failed."""


class SocialConnectedCallbackResponse(BaseModel):
    ok: bool
    connected: bool
    state: Literal["connected", "not_connected", "verify_failed_temp", "pending_oauth"] = "not_connected"
    message: Optional[str] = None


class AnalyticsOut(BaseModel):
    """GET /analytics — lightweight in-app metrics."""

    total_campaigns: int
    total_posts: int
    posts_published: int
    posts_failed: int
    success_rate: int
    last_published_at: Optional[str] = None


class PostAnalyticsOut(BaseModel):
    """GET /post-analytics/{post_id} — per-post performance snapshot."""

    post_id: int
    likes: int
    comments: int
    shares: int
    impressions: int
    engagement_rate: float
    source: Literal["ayrshare", "placeholder"] = "placeholder"


class CampaignInsightsOut(BaseModel):
    """GET /campaign-insights/{campaign_id} — AI analysis for future campaigns."""

    campaign_id: int
    insights: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
