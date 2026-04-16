from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from backend.timeutil import is_valid_iana_timezone


class GenerateCampaignRequest(BaseModel):
    business_type: str = "Small Business"
    goal: str
    location: str
    platforms: List[str] = Field(default_factory=list)
    campaign_goal_category: Optional[Literal["lead_gen", "branding", "engagement", "sales"]] = Field(
        default=None,
        description="Wizard strategy step: primary outcome category for StrategyAgent.",
    )
    tone: str = Field(
        default="professional",
        max_length=40,
        description="Wizard strategy step: brand voice for strategy + content agents.",
    )
    frequency: str = "3 per week"
    # ISO date YYYY-MM-DD for first post week (optional)
    start_date: Optional[str] = None
    audience: Optional[str] = None
    # IANA timezone string (e.g. "America/Los_Angeles")
    timezone: Optional[str] = None
    # Preferred posting hour in user's local time (0-23)
    post_hour: int = 10
    # Wizard schedule step — "Post now": schedule each post within minutes of generation
    post_immediately: bool = False
    # AI / media toggles (wizard)
    ai_text_enabled: bool = True
    ai_images_enabled: bool = True
    video_scripts_enabled: bool = True
    # Brand files uploaded in wizard Step 2 (optional); stored server-side as BrandAsset rows
    brand_asset_ids: Optional[List[int]] = None
    # New wizard: visual + template context (optional)
    selected_template: Optional[str] = None
    selected_caption_hook: Optional[str] = None
    persona: Optional[str] = None
    bucket: Optional[str] = None
    wizard_template: Optional[Dict[str, Any]] = None  # {id, name, bg}
    unsplash_selection: Optional[Dict[str, Any]] = None  # {id, url, thumb_url, download_url}
    wizard_video_url: Optional[str] = None
    """Wizard step 2 consolidated snapshot (template + optional stock image + optional video). Mirrors other fields for orchestration."""
    wizard_step2_media: Optional[Dict[str, Any]] = None
    # Step 3 AI captions from wizard (optional hints for the content agent)
    wizard_ai_captions: Optional[List[str]] = None
    # Step 2 video: Sora prompt + duration (optional; used for similar reels on posts 2+)
    wizard_video_prompt: Optional[str] = None
    wizard_video_duration_s: Optional[int] = None

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


class BrandAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    kind: str
    original_filename: str
    content_type: str
    size_bytes: int
    created_at: Optional[datetime] = None


class BrandAssetListResponse(BaseModel):
    items: List[BrandAssetOut] = Field(default_factory=list)


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[int] = None
    campaign_id: Optional[int] = None
    day: Optional[str] = None
    caption: str
    hashtags: List[str] = Field(default_factory=list)
    image_url: Optional[str] = ""
    video_url: Optional[str] = ""
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
    slides: List[Dict[str, Any]] = Field(default_factory=list)
    is_carousel: bool = False


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
    password: str = Field(min_length=8, max_length=128)
    timezone: Optional[str] = None
    # RBAC: account type chosen at signup (ignored when invite_token is present)
    account_type: str = Field(default="individual")
    # Team/org name (required when account_type != "individual" and no invite_token)
    team_name: Optional[str] = Field(default=None, max_length=120)
    # Invite token — when present, bypasses normal account_type logic
    invite_token: Optional[str] = None

    @field_validator("password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        if not isinstance(v, str):
            raise ValueError("Password is required.")
        s = v  # do not strip — spaces may be meaningful
        if len(s) < 8:
            raise ValueError("Password must be at least 8 characters.")
        if s.lower() in {"password", "12345678", "qwertyui", "abc12345", "password1"}:
            raise ValueError("Password is too common. Please choose a stronger password.")
        return s

    @field_validator("account_type")
    @classmethod
    def _valid_account_type(cls, v: str) -> str:
        v = str(v).strip().lower()
        if v not in ("individual", "team", "org"):
            raise ValueError("account_type must be 'individual', 'team', or 'org'")
        return v

    @field_validator("team_name")
    @classmethod
    def _team_name_sanitize(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s or None

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
    # Brand kit (wizard Media & Design step)
    brand_logo_url: str = ""
    brand_primary_color: str = ""
    brand_secondary_color: str = ""
    brand_font: str = ""
    brand_voice: str = ""
    brand_source: str = ""


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


# ---------------------------------------------------------------------------
# New Campaign Wizard (Phase 1) schemas
# ---------------------------------------------------------------------------


class PrefillFromPromptRequest(BaseModel):
    prompt: str = Field(min_length=4, max_length=600)


class PrefillFromPromptResponse(BaseModel):
    name: str = ""
    objective: str = ""
    target_audience: str = ""
    tone: str = "professional"
    platforms: List[str] = Field(default_factory=list)
    content_type: str = "single_image"  # single_image|carousel|video|story|text
    keywords: List[str] = Field(default_factory=list)


class CaptionsRequest(BaseModel):
    objective: str
    target_audience: str = ""
    tone: str = "professional"   # professional|friendly|playful|bold|luxury
    platform: str = "instagram"
    count: int = Field(default=5, ge=1, le=8)
    extra_context: str = ""


class CaptionsResponse(BaseModel):
    captions: List[str] = Field(default_factory=list)
    hashtags: List[str] = Field(default_factory=list)


class HooksRequest(BaseModel):
    objective: str
    target_audience: str = ""
    count: int = Field(default=5, ge=1, le=8)


class HooksResponse(BaseModel):
    hooks: List[str] = Field(default_factory=list)


class ModifyCaptionRequest(BaseModel):
    caption: str = Field(min_length=1)
    modifier: Literal[
        "shorter", "longer", "add_emojis", "remove_emojis",
        "add_hashtags", "more_professional", "more_playful",
        "add_cta", "rewrite"
    ] = "shorter"
    platform: str = "instagram"


class ModifyCaptionResponse(BaseModel):
    caption: str


class CaptionVariantOut(BaseModel):
    """CaptionAgent preview item: body copy + hashtag list (no # prefix required in JSON)."""

    caption: str = ""
    hashtags: List[str] = Field(default_factory=list)


class PreviewCaptionsRequest(BaseModel):
    """Wizard Step 3 — inputs for CaptionAgent preview (pre-full LangGraph run)."""

    bucket: str = "real_estate"
    persona: str = ""
    goal: str = ""
    location: str = ""
    platforms: List[str] = Field(default_factory=list)
    tone: str = "professional"
    count: int = 3
    campaign_goal_category: Optional[str] = Field(
        default=None,
        max_length=40,
        description="From wizard strategy — aligns with StrategyAgent output category.",
    )
    selected_template: str = ""
    wizard_template: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Wizard Step 2 template card — visual context for CaptionAgent.",
    )
    selected_caption_hook: str = Field(
        default="",
        description="Optional hook phrase from bucket defaults the user selected earlier.",
    )
    has_media_attachment: bool = Field(
        default=False,
        description="True when Step 2 includes an AI video attachment for the first post.",
    )


class PreviewCaptionsResponse(BaseModel):
    """Caption + hashtags per variation; ``captions`` remains a joined string list for legacy UIs."""

    captions: List[str] = Field(default_factory=list)
    variants: List[CaptionVariantOut] = Field(default_factory=list)


class PreviewScoreRequest(BaseModel):
    caption: str
    platforms: List[str] = Field(default_factory=list)
    has_media: bool = True
    objective: str = ""
    target_audience: str = ""


class PreviewScoreResponse(BaseModel):
    score: int = 0                 # 0-100
    grade: str = "B"               # A+|A|B+|B|C|D
    reasons: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)


class BrandKitUpdateRequest(BaseModel):
    brand_logo_url: Optional[str] = None
    brand_primary_color: Optional[str] = None
    brand_secondary_color: Optional[str] = None
    brand_font: Optional[str] = None
    brand_voice: Optional[str] = None
    brand_source: Optional[Literal["upload", "ai", ""]] = None


class BrandKitAIRequest(BaseModel):
    industry: str = "Real Estate"
    vibe: str = "modern"   # modern|luxury|friendly|bold|minimal
    primary_color_hint: str = ""  # optional hex


class UnsplashPhoto(BaseModel):
    id: str
    url: str
    thumb_url: str
    download_url: str
    author: str
    author_url: str = ""


class UnsplashSearchResponse(BaseModel):
    photos: List[UnsplashPhoto] = Field(default_factory=list)
    total: int = 0


class VideoGenerateRequest(BaseModel):
    prompt: str = Field(min_length=4, max_length=600)
    duration_seconds: int = Field(default=5, ge=3, le=12)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "9:16"


class VideoGenerateResponse(BaseModel):
    status: Literal["queued", "processing", "succeeded", "failed"] = "queued"
    video_url: str = ""
    prediction_id: str = ""
    error: str = ""


# ---------- AI Campaign Assistant (sidebar chat) ----------

class AssistantChatMessage(BaseModel):
    role: Literal["user", "assistant"] = "user"
    content: str = ""


class AssistantWizardState(BaseModel):
    """Snapshot of whatever the wizard currently has. All fields optional."""
    name: str = ""
    goal: str = ""
    prompt: str = ""
    content_type: str = ""
    tone: str = ""
    platforms: List[str] = Field(default_factory=list)
    location: str = ""
    start_date: str = ""
    duration: str = ""  # "single" | "7d" | "14d" | "30d"
    captions: List[str] = Field(default_factory=list)
    hooks: List[str] = Field(default_factory=list)
    selected_caption: str = ""
    brand_primary_color: str = ""
    brand_secondary_color: str = ""
    brand_font: str = ""
    brand_voice: str = ""


class AssistantChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    state: AssistantWizardState = Field(default_factory=AssistantWizardState)
    history: List[AssistantChatMessage] = Field(default_factory=list)


class AssistantPatch(BaseModel):
    """Partial wizard state the assistant wants to apply. All fields optional."""
    name: Optional[str] = None
    goal: Optional[str] = None
    prompt: Optional[str] = None
    content_type: Optional[str] = None
    tone: Optional[str] = None
    platforms: Optional[List[str]] = None
    location: Optional[str] = None
    start_date: Optional[str] = None
    duration: Optional[str] = None
    selected_caption: Optional[str] = None
    captions_append: Optional[List[str]] = None
    hooks_append: Optional[List[str]] = None


class AssistantChatResponse(BaseModel):
    reply: str = ""
    patch: AssistantPatch = Field(default_factory=AssistantPatch)
    suggestions: List[str] = Field(default_factory=list)


# ---------- Campaign Templates (save-as / duplicate) ----------

class TemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=400)
    payload: Dict[str, Any] = Field(default_factory=dict)


class TemplateUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=400)
    payload: Optional[Dict[str, Any]] = None


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str = ""
    description: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class TemplateListResponse(BaseModel):
    items: List[TemplateOut] = Field(default_factory=list)
    total: int = 0


class DuplicateCampaignRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    include_posts: bool = False  # default: clone only metadata, not posts


# --------- Phase 2 #4: Comment-to-DM automations ---------

CommentMatchMode = Literal["any", "all", "exact"]


class CommentAutomationCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    post_id: Optional[int] = None
    platform: str = Field(default="instagram", max_length=32)
    external_post_id: str = Field(default="", max_length=120)
    keywords: List[str] = Field(default_factory=list)
    match_mode: CommentMatchMode = "any"
    case_sensitive: bool = False
    reply_comment_enabled: bool = True
    reply_comment_template: str = Field(
        default="Thanks for the interest! Just sent you a DM 📩",
        max_length=500,
    )
    dm_enabled: bool = True
    dm_template: str = Field(
        default="Hi {handle}! Here's the info you asked about: {link}",
        max_length=1000,
    )
    link_url: str = Field(default="", max_length=500)
    is_active: bool = True

    @field_validator("keywords")
    @classmethod
    def _kw_clean(cls, v: List[str]) -> List[str]:
        out = []
        for k in v or []:
            s = (k or "").strip()
            if s and len(s) <= 80:
                out.append(s)
        if not out:
            raise ValueError("At least one keyword is required")
        if len(out) > 20:
            raise ValueError("Maximum 20 keywords")
        return out


class CommentAutomationUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    post_id: Optional[int] = None
    platform: Optional[str] = Field(default=None, max_length=32)
    external_post_id: Optional[str] = Field(default=None, max_length=120)
    keywords: Optional[List[str]] = None
    match_mode: Optional[CommentMatchMode] = None
    case_sensitive: Optional[bool] = None
    reply_comment_enabled: Optional[bool] = None
    reply_comment_template: Optional[str] = Field(default=None, max_length=500)
    dm_enabled: Optional[bool] = None
    dm_template: Optional[str] = Field(default=None, max_length=1000)
    link_url: Optional[str] = Field(default=None, max_length=500)
    is_active: Optional[bool] = None


class CommentAutomationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    name: str
    post_id: Optional[int] = None
    platform: str = "instagram"
    external_post_id: str = ""
    keywords: List[str] = Field(default_factory=list)
    match_mode: str = "any"
    case_sensitive: bool = False
    reply_comment_enabled: bool = True
    reply_comment_template: str = ""
    dm_enabled: bool = True
    dm_template: str = ""
    link_url: str = ""
    is_active: bool = True
    trigger_count: int = 0
    last_triggered_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class CommentAutomationListResponse(BaseModel):
    items: List[CommentAutomationOut] = Field(default_factory=list)
    total: int = 0


class CommentSimulateRequest(BaseModel):
    commenter_handle: str = Field(default="test_user", max_length=120)
    commenter_id: str = Field(default="", max_length=120)
    comment_text: str = Field(min_length=1, max_length=2000)
    external_comment_id: str = Field(default="", max_length=120)
    execute: bool = False  # if false, dry-run: record match but don't log as sent


class CommentSimulateResponse(BaseModel):
    matched: bool = False
    matched_keyword: str = ""
    rendered_reply: str = ""
    rendered_dm: str = ""
    trigger_id: Optional[int] = None
    reply_sent: bool = False
    dm_sent: bool = False
    reply_error: str = ""
    dm_error: str = ""


class CommentTriggerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    automation_id: int
    commenter_handle: str = ""
    commenter_id: str = ""
    comment_text: str = ""
    external_comment_id: str = ""
    matched_keyword: str = ""
    reply_sent: bool = False
    reply_error: str = ""
    dm_sent: bool = False
    dm_error: str = ""
    triggered_at: datetime


class CommentTriggerListResponse(BaseModel):
    items: List[CommentTriggerOut] = Field(default_factory=list)
    total: int = 0


class CommentWebhookPayload(BaseModel):
    """Loose shape for Ayrshare-style comment webhooks."""
    platform: str = "instagram"
    external_post_id: str = ""
    external_comment_id: str = ""
    commenter_handle: str = ""
    commenter_id: str = ""
    comment_text: str = ""


# --------- Phase 2 #5: Carousel slides ---------

class CarouselSlide(BaseModel):
    image_url: str = ""
    caption_overlay: str = Field(default="", max_length=300)
    alt_text: str = Field(default="", max_length=200)
    order: int = 0


class UpdateSlidesRequest(BaseModel):
    slides: List[CarouselSlide] = Field(default_factory=list)
    is_carousel: Optional[bool] = None

    @field_validator("slides")
    @classmethod
    def _limit_slides(cls, v):
        if len(v) > 20:
            raise ValueError("Max 20 slides per carousel")
        return v


class GenerateSlidesRequest(BaseModel):
    count: int = Field(default=5, ge=2, le=10)
    theme: str = Field(default="", max_length=400)
    style: str = Field(default="educational", max_length=60)  # educational | story | tips | listicle
    use_existing_caption: bool = True


class GenerateSlidesResponse(BaseModel):
    slides: List[CarouselSlide] = Field(default_factory=list)


# ---------- Wizard visual templates (Step 2) ----------


class WowManusPersonalizeRequest(BaseModel):
    """Persona + bucket context for wizard visual template generation."""

    persona: str = ""
    bucket: str = "creator"
    persona_goal: Optional[str] = None


class ManusVisualTemplateOut(BaseModel):
    id: str
    name: str
    tag: str = "Template"
    bg: str
    image_url: Optional[str] = Field(
        default=None,
        description="HTTPS URL for card art (Unsplash regular URL by default, or OpenAI when configured); UI falls back to bg if absent.",
    )


class WowManusVisualTemplatesResponse(BaseModel):
    """Template cards for the wizard \"Choose your visuals\" step (OpenAI + Unsplash, or legacy sources)."""

    source: Literal["openai_unsplash", "manus", "fallback", "unavailable"] = "fallback"
    error: str = ""
    templates: List[ManusVisualTemplateOut] = Field(default_factory=list)
