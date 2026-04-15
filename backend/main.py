import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

import backend.env_loader  # noqa: F401 — loads project root .env before agent imports

from backend.core.logger import configure_logging, get_logger, log_event, time_block
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from backend.agents.analytics_insights import run_campaign_insights
from backend.agents.errors import CampaignPipelineError, OpenAINotConfiguredError
from backend.agents.graph import resume_campaign_publishing, run_campaign_phase1
from backend.agents.nodes import _openai_api_key
from backend.ai.compliance import check_caption_compliance
from backend.auth import (
    create_access_token,
    get_current_user,
    get_user_by_email,
    hash_password,
    verify_password,
)
from backend.db import create_db_and_tables, engine, get_session
from backend.integrations.ayrshare import (
    coerce_ayrshare_platforms,
    platform_response_json,
)
from backend.services.publish_service import (
    _lock_expired,
    _utc_now_naive,
    safe_publish_post,
)
from backend.services.scheduler import publish_due_posts
from backend.workflow.post_state import (
    POST_APPROVED,
    POST_FAILED,
    POST_PUBLISHED,
    POST_PUBLISHING,
    POST_REVIEW,
    transition_post_status,
)
from backend.timeutil import is_valid_iana_timezone, normalize_iana_timezone
from backend.models import (
    BrandAsset,
    Campaign,
    CampaignTemplate,
    CanvaDesign,
    CommentAutomation,
    CommentTrigger,
    Lead,
    LeadForm,
    Post,
    SocialAccount,
    Team,
    TeamInvite,
    User,
)
from backend.permissions import check_permission, require_permission
from backend.schemas import (
    AnalyticsBulkUpdateOut,
    AnalyticsOut,
    AnalyticsPostRow,
    AnalyticsSummaryOut,
    ApproveCampaignRequest,
    BrandAssetListResponse,
    BrandAssetOut,
    BrandKitAIRequest,
    BrandKitUpdateRequest,
    CampaignInsightsOut,
    CampaignDetailOut,
    CampaignOut,
    CaptionsRequest,
    CaptionsResponse,
    CheckComplianceRequest,
    CheckComplianceResponse,
    ConnectSocialResponse,
    GenerateCampaignRequest,
    GenerateCampaignResponse,
    HooksRequest,
    HooksResponse,
    InviteLookupOut,
    InviteMemberRequest,
    InviteOut,
    LoginRequest,
    MemberOut,
    ModifyCaptionRequest,
    ModifyCaptionResponse,
    PerformanceAnalyticsAIOut,
    PostAnalyticsOut,
    PostOut,
    PrefillFromPromptRequest,
    PrefillFromPromptResponse,
    PreviewCaptionsRequest,
    PreviewCaptionsResponse,
    PreviewScoreRequest,
    PreviewScoreResponse,
    SignupRequest,
    SocialConnectedCallbackResponse,
    SocialStatusResponse,
    TeamOut,
    TokenResponse,
    UnsplashSearchResponse,
    UpdatePostRequest,
    UpdateProfileUrlsRequest,
    UpdateRoleRequest,
    UserOut,
    VideoGenerateRequest,
    VideoGenerateResponse,
    AssistantChatRequest,
    AssistantChatResponse,
    AssistantPatch,
    TemplateCreateRequest,
    TemplateUpdateRequest,
    TemplateOut,
    TemplateListResponse,
    DuplicateCampaignRequest,
    LeadFormField,
    LeadFormCreateRequest,
    LeadFormUpdateRequest,
    LeadFormOut,
    LeadFormListResponse,
    LeadSubmitRequest,
    LeadSubmitResponse,
    LeadOut,
    LeadListResponse,
    AttachLeadFormRequest,
    CommentAutomationCreateRequest,
    CommentAutomationUpdateRequest,
    CommentAutomationOut,
    CommentAutomationListResponse,
    CommentSimulateRequest,
    CommentSimulateResponse,
    CommentTriggerOut,
    CommentTriggerListResponse,
    CommentWebhookPayload,
    CarouselSlide,
    UpdateSlidesRequest,
    GenerateSlidesRequest,
    GenerateSlidesResponse,
    CanvaDesignCreateRequest,
    CanvaDesignImportRequest,
    CanvaDesignOut,
    CanvaDesignListResponse,
    CanvaStatusResponse,
)
from backend.integrations.unsplash import search_photos as unsplash_search_photos
from backend.integrations.replicate_video import (
    create_video_prediction,
    fetch_prediction as fetch_video_prediction,
)
from backend.services.team_service import (
    create_team,
    effective_team_role,
    list_members,
    remove_member,
    resolve_ayrshare_subject_user,
    update_member_role,
)
from backend.services.brand_asset_service import (
    assert_owned_asset_ids,
    brand_dir_for_user,
    disk_path,
    guess_content_type,
    new_stored_filename,
    normalize_kind,
    validate_upload,
)
from backend.services.invite_service import (
    create_invite,
    get_invite_by_token,
    validate_and_redeem_invite,
    INVITE_TTL_HOURS,
)
from backend.services.ayrshare_service import (
    AyrshareServiceError,
    create_ayrshare_profile,
    fetch_active_social_accounts,
    fetch_profiles_by_ref_id,
    generate_social_connect_url,
)
from backend.services.ai_analytics_service import analyze_performance
from backend.services.analytics import (
    fetch_post_analytics,
    get_analytics_payload,
    update_post_analytics,
)
from backend.services.analytics_service import (
    build_analytics_summary,
    list_user_posts_for_analytics,
    performance_tier,
    posts_as_ai_payload,
)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # refresh if needed

configure_logging()
log = get_logger("brokerai")

# Boot timestamp and app version, used by /health.
APP_BOOT_TIME = time.time()
APP_VERSION = os.getenv("APP_VERSION", os.getenv("RENDER_GIT_COMMIT", "dev"))[:12]

# Per-user daily cap on campaign generation (UTC day). Cleared on process restart.
_MAX_CAMPAIGNS_PER_USER_PER_DAY = 10
_daily_generate_count: Dict[Tuple[int, str], int] = {}

BASE_DIR = Path(__file__).resolve().parent.parent


def _public_app_origin() -> str:
    """HTTPS origin for Ayrshare JWT redirect (e.g. https://app.example.com)."""
    return (os.getenv("BROKERAI_PUBLIC_ORIGIN") or "").strip().rstrip("/")


def _connect_redirect_url() -> Optional[str]:
    base = _public_app_origin()
    if not base:
        return None
    return f"{base}/connect.html?returned=1"


def _upsert_social_account(
    session: Session,
    *,
    user_id: int,
    platform: str,
    is_connected: bool,
    profile_key: str,
) -> SocialAccount:
    row = session.exec(
        select(SocialAccount).where(
            SocialAccount.user_id == user_id,
            SocialAccount.platform == platform,
        )
    ).first()
    now = datetime.utcnow()
    if row is None:
        row = SocialAccount(
            user_id=user_id,
            platform=platform,
            is_connected=bool(is_connected),
            profile_key=(profile_key or "").strip(),
            created_at=now,
            updated_at=now,
        )
    else:
        row.is_connected = bool(is_connected)
        row.profile_key = (profile_key or "").strip()
        row.updated_at = now
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _get_social_account(session: Session, user_id: int, platform: str = "ayrshare_profile") -> Optional[SocialAccount]:
    return session.exec(
        select(SocialAccount).where(
            SocialAccount.user_id == user_id,
            SocialAccount.platform == platform,
        )
    ).first()


def _social_state_from_row(row: Optional[SocialAccount], *, sync_ok: bool) -> str:
    if not sync_ok:
        return "verify_failed_temp"
    if row is None:
        return "not_connected"
    if row.is_connected:
        return "connected"
    if (row.profile_key or "").strip():
        return "pending_oauth"
    return "not_connected"


def _verify_user_social_connection(session: Session, user: User) -> Tuple[bool, bool]:
    """
    Verify social connection against Ayrshare.
    Strategy:
      1. If a profile_key is stored (on the user or in social_accounts), use
         GET /api/user (Profile-Key header) as the authoritative source —
         this avoids the unreliable GET /profiles?refId= filter.
      2. Fall back to GET /profiles?refId= only when no key is known.
    Returns (is_connected, ayrshare_sync_ok).
    """
    uid = int(user.id or 0)
    if uid <= 0:
        return False, False

    # Re-read fresh from DB to avoid session-cache staleness
    db_user = session.get(User, uid)
    pk = ((db_user and db_user.ayrshare_profile_key) or "").strip()

    # Also check social_accounts table as a fallback key source
    if not pk:
        row = _get_social_account(session, uid)
        if row:
            pk = (row.profile_key or "").strip()

    if pk:
        # Primary path: verify via profile key directly (most reliable)
        active_accounts = fetch_active_social_accounts(pk)
        if active_accounts is None:
            # Ayrshare API failed — preserve existing DB state, signal sync failure
            row = _get_social_account(session, uid)
            if row is not None:
                existing_connected = bool(row.is_connected and (row.profile_key or "").strip())
                user.social_connected = existing_connected
                session.add(user)
                session.commit()
            log.warning("verify_social: Ayrshare /user failed user_id=%s", uid)
            return bool(user.social_connected), False

        is_connected = len(active_accounts) > 0
        _upsert_social_account(
            session,
            user_id=uid,
            platform="ayrshare_profile",
            is_connected=is_connected,
            profile_key=pk,  # Always preserve the profile key
        )
        user.social_connected = is_connected
        session.add(user)
        session.commit()
        log.info(
            "verify_social user_id=%s profile_key_prefix=%s active_accounts=%s connected=%s",
            uid,
            pk[:8],
            active_accounts,
            is_connected,
        )
        return is_connected, True

    # Fallback path: no profile key known, try refId lookup
    ref_id = f"brokerai_user_{uid}"
    profiles = fetch_profiles_by_ref_id(ref_id)
    if profiles is None:
        log.warning("verify_social: Ayrshare /profiles failed user_id=%s", uid)
        return False, False

    has_profile = len(profiles) > 0
    # Without a profile key we cannot be truly connected
    is_connected = False
    _upsert_social_account(
        session,
        user_id=uid,
        platform="ayrshare_profile",
        is_connected=False,
        profile_key="",
    )
    user.social_connected = False
    session.add(user)
    session.commit()
    log.info(
        "verify_social user_id=%s has_profile=%s pk_empty=True connected=False",
        uid,
        has_profile,
    )
    return False, True


def _require_social_ready(session: Session, user_id: int) -> None:
    u = session.get(User, user_id)
    if not u:
        raise HTTPException(status_code=401, detail="Not authenticated")
    subject = resolve_ayrshare_subject_user(session, u)
    connected, _sync_ok = _verify_user_social_connection(session, subject)
    session.refresh(subject)
    if not connected:
        raise HTTPException(
            status_code=403,
            detail="Please connect your social accounts first.",
        )


def _user_out(session: Session, u: User) -> UserOut:
    """User profile for API responses; social_connected follows team owner's link for members."""
    subject = resolve_ayrshare_subject_user(session, u)
    connected, _ = _verify_user_social_connection(session, subject)
    session.refresh(u)
    session.refresh(subject)
    return UserOut(
        id=u.id,
        email=u.email,
        social_connected=bool(connected),
        timezone=normalize_iana_timezone(getattr(u, "timezone", None)),
        facebook_url=getattr(u, "facebook_url", None) or "",
        instagram_url=getattr(u, "instagram_url", None) or "",
        linkedin_url=getattr(u, "linkedin_url", None) or "",
        account_type=getattr(u, "account_type", None) or "individual",
        role=effective_team_role(session, u),
        team_id=getattr(u, "team_id", None),
    )


def _hashtags_to_list(raw: str) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _compliance_issues_list(raw: str) -> List[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data]
    except json.JSONDecodeError:
        pass
    return []


def _platform_response_dict(raw: str) -> Optional[dict]:
    if not raw or raw.strip() in ("", "{}"):
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"value": data}
    except json.JSONDecodeError:
        return None


def _publish_platforms_list(raw: Any) -> list:
    return coerce_ayrshare_platforms(raw)


def _primary_platform_for_post(row: Post) -> str:
    px = (getattr(row, "platform", None) or "").strip()
    if px:
        return px
    pl = _publish_platforms_list(row.publish_platforms)
    return pl[0] if pl else ""


def _post_to_out(row: Post, day: Optional[str] = None) -> PostOut:
    body = (getattr(row, "content", None) or row.caption or "").strip()
    return PostOut(
        id=row.id,
        campaign_id=row.campaign_id,
        day=day or row.day_label,
        caption=row.caption,
        hashtags=_hashtags_to_list(row.hashtags),
        image_url=row.image_url or "",
        video_script=row.video_script or "",
        publish_platforms=_publish_platforms_list(row.publish_platforms),
        status=row.status,
        created_at=row.created_at,
        scheduled_at=row.scheduled_at,
        published_at=row.published_at,
        platform_response=_platform_response_dict(row.platform_response),
        compliance_passed=row.compliance_passed,
        compliance_issues=_compliance_issues_list(row.compliance_issues),
        last_error=(getattr(row, "last_error", None) or "").strip() or None,
        is_locked=bool(getattr(row, "is_locked", False)),
        next_publish_attempt_at=getattr(row, "next_publish_attempt_at", None),
        platform=_primary_platform_for_post(row),
        post_id=(getattr(row, "social_post_id", None) or "").strip(),
        content=body,
        likes=int(row.likes or 0),
        comments=int(row.comments or 0),
        shares=int(row.shares or 0),
        impressions=int(row.impressions or 0),
        engagement_rate=float(row.engagement_rate or 0),
        slides=_slides_list(getattr(row, "slides", None)),
        is_carousel=bool(getattr(row, "is_carousel", False)),
    )


def _slides_list(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except Exception:
        return []


async def _scheduler_loop() -> None:
    while True:
        try:
            await publish_due_posts()
        except Exception:
            log.exception("scheduler tick failed")
        else:
            log.debug("scheduler tick completed")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    # Security check: warn if JWT secret is insecure
    jwt_key = os.environ.get("JWT_SECRET_KEY", "")
    if not jwt_key or "change-me" in jwt_key or len(jwt_key) < 20:
        log.warning(
            "*** SECURITY WARNING: JWT_SECRET_KEY is missing or insecure. "
            "Set a strong random secret (32+ chars) in .env for production. ***"
        )
    task = None
    if not os.getenv("BROKERAI_DISABLE_SCHEDULER"):
        task = asyncio.create_task(_scheduler_loop())
        log.info("Background publish scheduler started (60s tick)")
    else:
        log.info("Background publish scheduler disabled (BROKERAI_DISABLE_SCHEDULER)")
    yield
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="BrokerAI", lifespan=lifespan)

# CORS: In strict/prod mode, ALLOWED_ORIGINS must be an explicit allowlist
# (wildcards are rejected). In local dev, default to "*" for convenience.
def _is_strict_env() -> bool:
    mode = (os.getenv("BROKERAI_ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
    if mode in ("prod", "production", "staging"):
        return True
    return (os.getenv("BROKERAI_STRICT_ENV") or "").strip().lower() in ("1", "true", "yes", "on")


def _build_cors_origins() -> list[str]:
    raw = os.environ.get("ALLOWED_ORIGINS", "").strip()
    strict = _is_strict_env()

    if raw:
        origins = [o.strip() for o in raw.split(",") if o.strip()]
    else:
        origins = [] if strict else ["*"]

    # Merge the app's own public origin so the frontend is always permitted.
    pub = _public_app_origin()
    if pub and pub not in origins and "*" not in origins:
        origins.append(pub)

    if strict and ("*" in origins or not origins):
        # Fail loud — running in prod with "*" is dangerous.
        raise RuntimeError(
            "CORS misconfigured: set ALLOWED_ORIGINS to an explicit comma-separated list "
            "(wildcards are forbidden in prod/strict mode)."
        )
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=_build_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Security headers ------------------------------------------------------
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.types import ASGIApp  # noqa: E402


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach a conservative set of security headers to every response.

    CSP is intentionally permissive for 'unsafe-inline'/'unsafe-eval' because
    the current frontend is vanilla HTML with inline scripts/styles. Tighten
    when the UI migrates to a bundled frontend.
    """

    def __init__(self, app: ASGIApp, *, csp: Optional[str] = None) -> None:
        super().__init__(app)
        self.csp = csp or (
            "default-src 'self'; "
            "img-src 'self' data: blob: https:; "
            "media-src 'self' blob: https:; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https:; "
            "style-src 'self' 'unsafe-inline' https:; "
            "font-src 'self' data: https:; "
            "connect-src 'self' https:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self';"
        )

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=()",
        )
        # HSTS only makes sense over HTTPS — harmless for local http but
        # useful once the app is behind TLS.
        if _is_strict_env():
            headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        headers.setdefault("Content-Security-Policy", self.csp)
        return response


app.add_middleware(SecurityHeadersMiddleware)


# --- Rate limiting ---------------------------------------------------------
# slowapi is optional — if missing, we no-op so local dev still works.
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler  # type: ignore
    from slowapi.errors import RateLimitExceeded  # type: ignore
    from slowapi.middleware import SlowAPIMiddleware  # type: ignore
    from slowapi.util import get_remote_address  # type: ignore

    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[],  # per-route only
        headers_enabled=True,
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    _RATE_LIMITS_ENABLED = True
except Exception as _rl_exc:  # pragma: no cover
    log.warning("slowapi not available, rate limits disabled: %s", _rl_exc)

    class _NoopLimiter:
        def limit(self, *_args, **_kwargs):
            def deco(fn):
                return fn
            return deco

    limiter = _NoopLimiter()  # type: ignore[assignment]
    _RATE_LIMITS_ENABLED = False

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/favicon.ico")
async def serve_favicon():
    # Serve the SVG favicon for /favicon.ico requests (modern browsers accept SVG).
    return FileResponse(BASE_DIR / "static" / "img" / "favicon.svg", media_type="image/svg+xml")


@app.get("/favicon.svg")
async def serve_favicon_svg():
    return FileResponse(BASE_DIR / "static" / "img" / "favicon.svg", media_type="image/svg+xml")


@app.get("/robots.txt")
async def serve_robots():
    return PlainTextResponse("User-agent: *\nAllow: /\n", media_type="text/plain")


@app.get("/")
async def serve_index():
    return FileResponse(BASE_DIR / "frontend" / "index.html")


@app.get("/wizard-v2.html")
def wizard_v2_page():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "wizard-v2.html")


@app.get("/wizard.html")
async def serve_wizard():
    return FileResponse(BASE_DIR / "frontend" / "wizard.html")


@app.get("/review.html")
async def serve_review():
    return FileResponse(BASE_DIR / "frontend" / "review.html")


@app.get("/dashboard.html")
async def serve_dashboard():
    return FileResponse(BASE_DIR / "frontend" / "dashboard.html")


@app.get("/analytics.html")
async def serve_analytics_page():
    return FileResponse(BASE_DIR / "frontend" / "analytics.html")


@app.get("/login.html")
async def serve_login():
    return FileResponse(BASE_DIR / "frontend" / "login.html")


@app.get("/signup.html")
async def serve_signup():
    return FileResponse(BASE_DIR / "frontend" / "signup.html")


@app.get("/forgot-password.html")
async def serve_forgot_password():
    return FileResponse(BASE_DIR / "frontend" / "forgot-password.html")


@app.get("/connect.html")
async def serve_connect():
    return FileResponse(BASE_DIR / "frontend" / "connect.html")


@app.get("/privacy.html")
async def serve_privacy():
    return FileResponse(BASE_DIR / "frontend" / "privacy.html")


@app.get("/cookies.html")
async def serve_cookies():
    return FileResponse(BASE_DIR / "frontend" / "cookies.html")


@app.get("/terms.html")
async def serve_terms():
    return FileResponse(BASE_DIR / "frontend" / "terms.html")


@app.get("/contact.html")
async def serve_contact():
    return FileResponse(BASE_DIR / "frontend" / "contact.html")


@app.post("/connect-social", response_model=ConnectSocialResponse)
async def connect_social(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Ensure an Ayrshare User Profile exists and return JWT SSO URL to link networks."""
    user = session.get(User, current_user.id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    subject = resolve_ayrshare_subject_user(session, user)
    if int(subject.id or 0) != int(user.id or 0):
        raise HTTPException(
            status_code=403,
            detail=(
                "Social accounts are managed by your team owner. "
                "Publishing uses their connected channels — you do not need to connect separately."
            ),
        )
    try:
        existing_row = _get_social_account(session, int(user.id or 0))
        if not (user.ayrshare_profile_key or "").strip() and existing_row is not None:
            cached_pk = (existing_row.profile_key or "").strip()
            if cached_pk:
                user.ayrshare_profile_key = cached_pk
                session.add(user)
                session.commit()
                session.refresh(user)
                log.info(
                    "connect-social reused cached profile key from social_accounts user_id=%s pk_prefix=%s",
                    current_user.id,
                    cached_pk[:8],
                )

        if not (user.ayrshare_profile_key or "").strip():
            # Before creating a new profile, check if this refId already exists in Ayrshare.
            ref_id = f"brokerai_user_{int(user.id or 0)}"
            existing_profiles = await asyncio.to_thread(fetch_profiles_by_ref_id, ref_id)
            if existing_profiles:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Ayrshare profile already exists for this user but profile key is unavailable "
                        "to restore automatically. Reconnect using the same app database/user record "
                        "or set the existing profile key from Ayrshare dashboard."
                    ),
                )
            pk = await asyncio.to_thread(
                create_ayrshare_profile, user.id, user.email
            )
            user.ayrshare_profile_key = pk
            session.add(user)
            session.commit()
            session.refresh(user)
        _upsert_social_account(
            session,
            user_id=int(user.id or 0),
            platform="ayrshare_profile",
            is_connected=False,
            profile_key=(user.ayrshare_profile_key or "").strip(),
        )
        redirect = _connect_redirect_url()
        url = await asyncio.to_thread(
            generate_social_connect_url,
            (user.ayrshare_profile_key or "").strip(),
            redirect,
        )
    except AyrshareServiceError as e:
        # Duplicate title fallback: try to recover with an existing cached profile key.
        msg = str(e.message or "")
        if "Profile title already exists" in msg:
            row = _get_social_account(session, int(user.id or 0))
            cached_pk = (row.profile_key if row else "") or (user.ayrshare_profile_key or "")
            if str(cached_pk).strip():
                user.ayrshare_profile_key = str(cached_pk).strip()
                session.add(user)
                session.commit()
                session.refresh(user)
                redirect = _connect_redirect_url()
                url = await asyncio.to_thread(
                    generate_social_connect_url,
                    (user.ayrshare_profile_key or "").strip(),
                    redirect,
                )
                log.info(
                    "connect-social duplicate-title recovered via cached key user_id=%s pk_prefix=%s",
                    current_user.id,
                    (user.ayrshare_profile_key or "")[:8],
                )
                return ConnectSocialResponse(connect_url=url)
        log.warning(
            "connect-social failed user_id=%s: %s",
            current_user.id,
            e.message,
        )
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except HTTPException:
        raise
    except Exception as e:
        log.exception("connect-social unexpected error user_id=%s", current_user.id)
        raise HTTPException(
            status_code=502,
            detail=f"Connect Accounts failed: {e}",
        ) from e
    log.info("connect-social success user_id=%s pk_prefix=%s", current_user.id, (user.ayrshare_profile_key or "")[:8])
    return ConnectSocialResponse(connect_url=url)


@app.get("/social-status", response_model=SocialStatusResponse)
def social_status(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    user = session.get(User, current_user.id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    subject = resolve_ayrshare_subject_user(session, user)
    connected, sync_ok = _verify_user_social_connection(session, subject)
    session.refresh(subject)
    row = _get_social_account(session, int(subject.id or 0))
    state = _social_state_from_row(row, sync_ok=sync_ok)
    resp = SocialStatusResponse(
        connected=bool(connected),
        state=state,  # type: ignore[arg-type]
        profile_key_present=bool((subject.ayrshare_profile_key or "").strip()),
        can_create_campaign=bool(connected),
        last_verified_at=(row.updated_at if row is not None else None),
        ayrshare_sync_ok=sync_ok,
    )
    log.info(
        "social-status requester_id=%s subject_user_id=%s connected=%s state=%s sync_ok=%s profile_key_present=%s",
        current_user.id,
        subject.id,
        resp.connected,
        resp.state,
        resp.ayrshare_sync_ok,
        resp.profile_key_present,
    )
    return resp


@app.post("/social-connected-callback", response_model=SocialConnectedCallbackResponse)
def social_connected_callback(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Re-sync linked networks from Ayrshare after the user finishes SSO linking."""
    user = session.get(User, current_user.id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    subject = resolve_ayrshare_subject_user(session, user)
    connected, sync_ok = _verify_user_social_connection(session, subject)
    session.refresh(subject)
    row = _get_social_account(session, int(subject.id or 0))
    state = _social_state_from_row(row, sync_ok=sync_ok)
    msg = None
    if not sync_ok:
        msg = "Could not verify with Ayrshare right now. Please retry."
    elif not connected:
        msg = "Authorization not completed yet. Finish in Ayrshare and retry."
    log.info(
        "social-connected-callback requester_id=%s subject_user_id=%s sync_ok=%s connected=%s state=%s",
        current_user.id,
        subject.id,
        sync_ok,
        connected,
        state,
    )
    return SocialConnectedCallbackResponse(
        ok=bool(sync_ok),
        connected=bool(connected),
        state=state,  # type: ignore[arg-type]
        message=msg,
    )


@app.post("/signup", response_model=TokenResponse)
@limiter.limit("10/hour")
def signup(request: Request, body: SignupRequest, session: Session = Depends(get_session)):
    email = body.email.strip().lower()
    tz = normalize_iana_timezone(getattr(body, "timezone", None))
    invite_token = (getattr(body, "invite_token", None) or "").strip() or None

    # ── CASE 2: Invite signup ──────────────────────────────────────────────
    if invite_token:
        # validate_and_redeem_invite raises HTTPException on any failure
        invite = validate_and_redeem_invite(session, token=invite_token, signup_email=email)

        if get_user_by_email(session, email):
            raise HTTPException(status_code=400, detail="Email already registered")

        user = User(
            email=email,
            password_hash=hash_password(body.password),
            timezone=tz,
            account_type="team",
            role="member",
            team_id=invite.team_id,
        )
        session.add(user)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(status_code=400, detail="Email already registered")

        # Commit invite redemption (is_used=True was set in validate_and_redeem_invite)
        session.refresh(user)
        log_event(
            "signup",
            kind="invite",
            user_id=user.id,
            team_id=invite.team_id,
            invite_id=invite.id,
            email=email,
        )
        return TokenResponse(access_token=create_access_token(user.id))

    # ── CASE 1: Normal signup ──────────────────────────────────────────────
    if get_user_by_email(session, email):
        raise HTTPException(status_code=400, detail="Email already registered")

    account_type = (getattr(body, "account_type", None) or "individual").lower()

    team_name = (getattr(body, "team_name", None) or "").strip()
    if account_type in ("team", "org") and not team_name:
        team_name = (
            email.split("@")[0].replace(".", " ").title()
            + ("'s Team" if account_type == "team" else "'s Organization")
        )

    user = User(
        email=email,
        password_hash=hash_password(body.password),
        timezone=tz,
        account_type=account_type,
        role="owner",
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=400, detail="Email already registered")
    session.refresh(user)

    if account_type in ("team", "org"):
        create_team(session, name=team_name, account_type=account_type, owner=user)
        session.refresh(user)

    log_event(
        "signup",
        kind="direct",
        user_id=user.id,
        account_type=account_type,
        email=email,
    )
    return TokenResponse(access_token=create_access_token(user.id))


@app.post("/login", response_model=TokenResponse)
@limiter.limit("20/minute")
def login(request: Request, body: LoginRequest, session: Session = Depends(get_session)):
    email = body.email.strip().lower()
    user = get_user_by_email(session, email)
    if not user or not verify_password(body.password, user.password_hash):
        log_event("login_failed", email=email, reason="bad_credentials", level=logging.WARNING)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    log_event("login", user_id=user.id, email=email)
    return TokenResponse(access_token=create_access_token(user.id))


@app.post("/auth/forgot-password")
@limiter.limit("5/hour")
def forgot_password(request: Request, body: Dict[str, Any]):
    """Stub password-reset entrypoint.

    Until full email-based reset is wired up we always return a generic
    success message (to avoid email-enumeration) and log the request so
    support can follow up manually.
    """
    email = str((body or {}).get("email") or "").strip().lower()
    support = os.getenv("SUPPORT_EMAIL", "support@brokerai.app")
    if not email or "@" not in email:
        # Intentionally the same response shape as the success path — do not
        # leak whether the account exists.
        return {
            "ok": True,
            "message": (
                "If that email exists, we'll send reset instructions shortly. "
                f"You can also email {support} for help."
            ),
        }
    log.info("password_reset_request email=%s", email)
    # TODO: generate a signed reset token and email it. For now we just return
    # a neutral message and the support contact.
    return {
        "ok": True,
        "message": (
            "If that email exists, we'll send reset instructions shortly. "
            f"In the meantime, email {support} and we'll help you recover your account."
        ),
    }


@app.get("/me", response_model=UserOut)
def me(
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    u = session.get(User, user.id)
    if u is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _user_out(session, u)


@app.patch("/me/profile-urls", response_model=UserOut)
def update_profile_urls(
    body: UpdateProfileUrlsRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    u = session.get(User, current_user.id)
    if u is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    data = body.model_dump(exclude_unset=True)
    if "facebook_url" in data:
        u.facebook_url = data["facebook_url"] or ""
    if "instagram_url" in data:
        u.instagram_url = data["instagram_url"] or ""
    if "linkedin_url" in data:
        u.linkedin_url = data["linkedin_url"] or ""
    session.add(u)
    session.commit()
    session.refresh(u)
    return _user_out(session, u)


PLAN_LIMITS = {
    "free": 2,      # 2 campaigns max
    "pro": 15,      # 15 campaigns/month
    "agency": 9999, # effectively unlimited
}


def _attach_brand_asset_summaries(
    session: Session, user_id: int, campaign_data: Dict[str, Any]
) -> None:
    """Populate brand_asset_summaries for LangGraph (filenames + kinds; no file bytes)."""
    raw_ids = campaign_data.get("brand_asset_ids") or []
    if not raw_ids:
        campaign_data["brand_asset_summaries"] = []
        return
    rows: List[BrandAsset] = []
    for raw in raw_ids:
        try:
            aid = int(raw)
        except (TypeError, ValueError):
            continue
        row = session.get(BrandAsset, aid)
        if row is not None and int(row.user_id or 0) == int(user_id):
            rows.append(row)
    campaign_data["brand_asset_summaries"] = [
        {
            "id": r.id,
            "kind": r.kind or "document",
            "filename": r.original_filename or "",
            "content_type": r.content_type or "",
        }
        for r in rows
    ]


@app.post("/generate-campaign", response_model=GenerateCampaignResponse)
@limiter.limit("30/hour")
async def generate_campaign(
    request: Request,
    body: GenerateCampaignRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    _require_social_ready(session, current_user.id)

    if not _openai_api_key():
        raise HTTPException(
            status_code=503,
            detail=(
                "OpenAI API is not configured. Set OPENAI_API_KEY to generate AI campaigns, "
                "captions, images, and video scripts."
            ),
        )
    if not body.ai_text_enabled:
        raise HTTPException(
            status_code=400,
            detail="AI captions and strategy must be enabled for campaign generation.",
        )
    if not body.ai_images_enabled:
        raise HTTPException(
            status_code=400,
            detail="AI images must be enabled for campaign generation.",
        )

    # Usage limit check (plan)
    plan = getattr(current_user, "plan", "free") or "free"
    limit = PLAN_LIMITS.get(plan, 2)
    camp_count = len(
        session.exec(
            select(Campaign).where(
                Campaign.user_id == current_user.id,
                Campaign.status != "failed",
            )
        ).all()
    )
    if camp_count >= limit:
        raise HTTPException(
            status_code=403,
            detail=f"Campaign limit reached ({limit} for {plan} plan). Upgrade to create more.",
        )

    db_user = session.get(User, current_user.id)
    if db_user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if body.timezone is not None and str(body.timezone).strip():
        if not is_valid_iana_timezone(str(body.timezone).strip()):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid IANA timezone: {body.timezone!r}",
            )
        campaign_tz = str(body.timezone).strip()
    else:
        campaign_tz = normalize_iana_timezone(db_user.timezone)
    if db_user.timezone != campaign_tz:
        db_user.timezone = campaign_tz
        session.add(db_user)
        session.commit()

    utc_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    gkey = (current_user.id, utc_day)
    used = _daily_generate_count.get(gkey, 0)
    if used >= _MAX_CAMPAIGNS_PER_USER_PER_DAY:
        raise HTTPException(
            status_code=429,
            detail=f"Daily campaign generation limit reached ({_MAX_CAMPAIGNS_PER_USER_PER_DAY} per day). Try again tomorrow.",
        )

    # RBAC: check create_campaign permission
    if not check_permission(current_user, "create_campaign"):
        raise HTTPException(status_code=403, detail="You do not have permission to create campaigns")

    try:
        assert_owned_asset_ids(session, current_user.id, body.brand_asset_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    fb_u = (getattr(db_user, "facebook_url", None) or "").strip()
    ig_u = (getattr(db_user, "instagram_url", None) or "").strip()
    li_u = (getattr(db_user, "linkedin_url", None) or "").strip()
    camp = Campaign(
        user_id=current_user.id,
        team_id=getattr(current_user, "team_id", None),
        created_by=current_user.id,
        status="draft",
        graph_thread_id="",
        name=f"{body.business_type} – {body.goal}"[:200],
        objective=body.goal,
        target_audience=body.audience or "",
        facebook_url=fb_u,
        instagram_url=ig_u,
        linkedin_url=li_u,
    )
    session.add(camp)
    session.commit()
    session.refresh(camp)
    thread_id = f"campaign-{camp.id}"
    camp.graph_thread_id = thread_id
    camp.updated_at = datetime.utcnow()
    session.add(camp)
    session.commit()

    campaign_data = {
        **body.model_dump(),
        "timezone": campaign_tz,
        "facebook_url": fb_u,
        "instagram_url": ig_u,
        "linkedin_url": li_u,
    }
    _attach_brand_asset_summaries(session, int(current_user.id or 0), campaign_data)
    initial = {
        "user_id": current_user.id,
        "campaign_id": camp.id,
        "approved": False,
        "campaign_data": campaign_data,
        "step_log": [],
        # Lead capture data from wizard Step 5 — consumed by lead_capture_node
        "lead_form_config": body.lead_form_config or {},
        "automation_config": body.automation_config or {},
    }
    log.info(
        "Campaign generation started user_id=%s campaign_id=%s goal=%s",
        current_user.id,
        camp.id,
        body.goal,
    )
    try:
        await asyncio.to_thread(run_campaign_phase1, initial, thread_id)
    except OpenAINotConfiguredError as e:
        log.warning("LangGraph phase1 missing OpenAI campaign_id=%s: %s", camp.id, e)
        camp.status = "failed"
        session.add(camp)
        session.commit()
        raise HTTPException(status_code=503, detail=str(e)) from e
    except CampaignPipelineError as e:
        log.warning("LangGraph phase1 pipeline error campaign_id=%s: %s", camp.id, e)
        camp.status = "failed"
        session.add(camp)
        session.commit()
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception:
        log.exception("LangGraph phase1 failed campaign_id=%s", camp.id)
        camp.status = "failed"
        session.add(camp)
        session.commit()
        raise HTTPException(status_code=500, detail="Campaign generation failed")

    session.expire_all()
    camp = session.get(Campaign, camp.id)
    stmt = (
        select(Post)
        .where(Post.campaign_id == camp.id)
        .order_by(Post.id.asc())
    )
    rows = session.exec(stmt).all()
    posts_out = [_post_to_out(r) for r in rows]
    _daily_generate_count[gkey] = used + 1
    log.info(
        "Campaign generation finished user_id=%s campaign_id=%s posts=%s",
        current_user.id,
        camp.id,
        len(posts_out),
    )
    return GenerateCampaignResponse(campaign_id=camp.id, posts=posts_out)


def _post_summary_for_insights(row: Post) -> Dict[str, Any]:
    tags = _hashtags_to_list(row.hashtags)
    cap = (row.caption or "").strip()
    if len(cap) > 600:
        cap = cap[:600] + "…"
    hour_utc: Optional[int] = None
    dt = row.published_at or row.scheduled_at
    if dt is not None:
        try:
            hour_utc = int(dt.hour)
        except Exception:
            hour_utc = None
    plats = row.publish_platforms if isinstance(row.publish_platforms, list) else []
    return {
        "post_id": row.id,
        "day_label": row.day_label or "",
        "status": row.status,
        "likes": int(row.likes or 0),
        "comments": int(row.comments or 0),
        "shares": int(row.shares or 0),
        "impressions": int(row.impressions or 0),
        "engagement_rate_pct": float(row.engagement_rate or 0),
        "caption_excerpt": cap,
        "hashtags": [str(t) for t in tags[:16]],
        "platforms": [str(p) for p in plats],
        "posted_or_scheduled_hour_utc": hour_utc,
    }


@app.get("/campaigns", response_model=List[CampaignOut])
async def list_campaigns(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Return all campaigns for the authenticated user, newest first."""
    stmt = (
        select(Campaign)
        .where(Campaign.user_id == current_user.id)
        .order_by(Campaign.created_at.desc())
    )
    campaigns = session.exec(stmt).all()
    return [CampaignOut.model_validate(c) for c in campaigns]


@app.get("/campaign/{campaign_id}", response_model=CampaignDetailOut)
def get_campaign(
    campaign_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    camp = session.get(Campaign, campaign_id)
    if not camp or camp.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    stmt = (
        select(Post)
        .where(Post.campaign_id == campaign_id)
        .order_by(Post.id.asc())
    )
    rows = session.exec(stmt).all()
    return CampaignDetailOut(
        campaign=CampaignOut.model_validate(camp),
        posts=[_post_to_out(r) for r in rows],
    )


@app.get("/campaign-insights/{campaign_id}", response_model=CampaignInsightsOut)
async def campaign_insights(
    campaign_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """AI-generated insights and recommendations from post metrics, captions, and hashtags."""
    camp = session.get(Campaign, campaign_id)
    if not camp or camp.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    stmt = (
        select(Post)
        .where(Post.campaign_id == campaign_id)
        .order_by(Post.id.asc())
    )
    rows = list(session.exec(stmt).all())
    summaries = [_post_summary_for_insights(r) for r in rows]
    result = await asyncio.to_thread(run_campaign_insights, summaries)
    return CampaignInsightsOut(
        campaign_id=campaign_id,
        insights=result.insights,
        recommendations=result.recommendations,
    )


@app.post("/approve-campaign")
async def approve_campaign(
    body: ApproveCampaignRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_permission("approve_campaign")),
):
    _require_social_ready(session, current_user.id)

    camp = session.get(Campaign, body.campaign_id)
    # Allow if user owns the campaign OR belongs to the same team as the campaign
    user_team_id = getattr(current_user, "team_id", None)
    camp_team_id = getattr(camp, "team_id", None) if camp else None
    owns_campaign = camp and camp.user_id == current_user.id
    same_team = camp and camp_team_id is not None and camp_team_id == user_team_id
    if not camp or (not owns_campaign and not same_team):
        raise HTTPException(status_code=404, detail="Campaign not found")
    if camp.status != "pending_approval":
        raise HTTPException(
            status_code=400,
            detail="Campaign is not waiting for approval",
        )
    tid = camp.graph_thread_id or f"campaign-{camp.id}"
    log.info(
        "Approve campaign requested user_id=%s campaign_id=%s thread_id=%s",
        current_user.id,
        camp.id,
        tid,
    )
    try:
        await asyncio.to_thread(
            resume_campaign_publishing, tid, camp.id, current_user.id
        )
    except Exception:
        log.exception(
            "campaign approval publish phase failed campaign_id=%s user_id=%s",
            camp.id,
            current_user.id,
        )
        raise HTTPException(
            status_code=500,
            detail="Could not finalize campaign approval. Please try again.",
        )
    # Record who approved
    camp.approved_by = current_user.id
    camp.updated_at = datetime.utcnow()
    session.add(camp)
    session.commit()
    return {
        "ok": True,
        "message": "Campaign approved — posts are queued for publishing.",
    }


@app.post("/check-compliance", response_model=CheckComplianceResponse)
async def check_compliance(
    body: CheckComplianceRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await check_caption_compliance(body.caption)
    if body.post_id is not None:
        row = session.get(Post, body.post_id)
        if not row or row.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Post not found")
        row.compliance_passed = result.passed
        row.compliance_checked_at = datetime.utcnow()
        session.add(row)
        session.commit()
    return result


@app.get("/posts", response_model=List[PostOut])
async def list_posts(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    stmt = (
        select(Post)
        .where(Post.user_id == current_user.id)
        .order_by(Post.created_at.desc())
    )
    rows = session.exec(stmt).all()
    return [_post_to_out(row, day=None) for row in rows]


@app.post("/approve-post/{post_id}", response_model=PostOut)
async def approve_post(
    post_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_permission("approve_campaign")),
):
    row = session.get(Post, post_id)
    if not row or row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Post not found")
    if row.status != POST_REVIEW:
        raise HTTPException(
            status_code=400,
            detail=f"Post must be in review to approve (current: {row.status})",
        )
    row.scheduled_at = row.scheduled_at or datetime.now(timezone.utc).replace(
        tzinfo=None
    )
    try:
        transition_post_status(
            session, row, POST_APPROVED, actor="api_approve_post", reason="user"
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.add(row)
    session.commit()
    session.refresh(row)
    return _post_to_out(row)


@app.post("/publish/{post_id}")
async def publish_post_now(
    post_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_permission("publish_campaign")),
):
    """Idempotent manual publish (bypasses schedule). Double-click safe."""
    row = session.get(Post, post_id)
    if not row or row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Post not found")
    _require_social_ready(session, current_user.id)
    if row.status == POST_PUBLISHED:
        return {"ok": True, "no_op": True, "message": "Already published"}
    if row.status == POST_PUBLISHING and row.is_locked:
        if not _lock_expired(row.lock_timestamp, _utc_now_naive()):
            raise HTTPException(
                status_code=409,
                detail="Publish already in progress for this post",
            )
    if row.status not in (POST_APPROVED, POST_FAILED):
        raise HTTPException(
            status_code=400,
            detail=f"Post not ready to publish (status={row.status})",
        )
    _pub_t0 = time.perf_counter()
    log_event(
        "publish.request",
        post_id=post_id,
        user_id=current_user.id,
        platform=row.platform,
    )
    out = await safe_publish_post(post_id, force_immediate=True)
    _pub_dur_ms = int((time.perf_counter() - _pub_t0) * 1000)
    if out.get("no_op"):
        log_event(
            "publish.complete",
            post_id=post_id,
            user_id=current_user.id,
            platform=row.platform,
            status=out.get("status"),
            no_op=True,
            duration_ms=_pub_dur_ms,
        )
        return {"ok": True, "no_op": True, "status": out.get("status")}
    if not out.get("ok") and out.get("error") == "not_eligible":
        log_event(
            "publish.failed",
            level=logging.WARNING,
            post_id=post_id,
            user_id=current_user.id,
            platform=row.platform,
            reason="not_eligible",
            duration_ms=_pub_dur_ms,
        )
        raise HTTPException(
            status_code=409,
            detail="Could not claim post for publish (in progress, max attempts, or not eligible)",
        )
    ok = bool(out.get("ok"))
    log_event(
        "publish.complete" if ok else "publish.failed",
        level=logging.INFO if ok else logging.WARNING,
        post_id=post_id,
        user_id=current_user.id,
        platform=row.platform,
        status=out.get("status"),
        duration_ms=_pub_dur_ms,
    )
    return {"ok": ok, "status": out.get("status")}


@app.post("/update-post/{post_id}", response_model=PostOut)
async def update_post(
    post_id: int,
    body: UpdatePostRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    row = session.get(Post, post_id)
    if not row or row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Post not found")
    if body.caption is not None:
        row.caption = body.caption
        row.content = body.caption
    if body.hashtags is not None:
        row.hashtags = json.dumps([str(t) for t in body.hashtags])
    session.add(row)
    session.commit()
    session.refresh(row)
    return _post_to_out(row)


@app.get("/analytics", response_model=AnalyticsOut)
def analytics(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Lightweight in-app analytics (no third-party analytics SDK)."""
    payload = get_analytics_payload(session, current_user.id)
    return AnalyticsOut(**payload)


@app.get("/post-analytics/{post_id}", response_model=PostAnalyticsOut)
async def post_analytics(
    post_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Per-post metrics from Ayrshare when available; otherwise placeholder values."""
    data = await fetch_post_analytics(session, post_id, current_user.id)
    if data.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Post not found")
    return PostAnalyticsOut(
        post_id=int(data["post_id"]),
        likes=int(data["likes"]),
        comments=int(data["comments"]),
        shares=int(data["shares"]),
        impressions=int(data["impressions"]),
        engagement_rate=float(data["engagement_rate"]),
        source=data.get("source") or "placeholder",
    )


@app.get("/analytics/posts", response_model=List[AnalyticsPostRow])
def analytics_posts(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """All posts with performance fields, sorted by engagement rate (desc)."""
    rows = list_user_posts_for_analytics(session, current_user.id)
    published_rates = [
        float(r.engagement_rate or 0) for r in rows if r.status == "published"
    ]
    out: List[AnalyticsPostRow] = []
    for r in rows:
        if r.id is None:
            continue
        text = (getattr(r, "content", None) or "").strip() or (r.caption or "")
        preview = text if len(text) <= 280 else text[:277] + "…"
        tier = performance_tier(
            float(r.engagement_rate or 0), r.status or "", published_rates
        )
        out.append(
            AnalyticsPostRow(
                id=int(r.id),
                post_id=(getattr(r, "social_post_id", None) or "").strip(),
                platform=_primary_platform_for_post(r),
                content=preview,
                created_at=r.created_at,
                likes=int(r.likes or 0),
                comments=int(r.comments or 0),
                impressions=int(r.impressions or 0),
                engagement_rate=float(r.engagement_rate or 0),
                status=r.status or "",
                performance_tier=tier,
            )
        )
    return out


@app.get("/analytics/summary", response_model=AnalyticsSummaryOut)
def analytics_summary(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    payload = build_analytics_summary(session, current_user.id)
    return AnalyticsSummaryOut(**payload)


@app.post("/analytics/update", response_model=AnalyticsBulkUpdateOut)
async def analytics_update(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Refresh metrics for all posts (Ayrshare when possible, else simulation)."""
    data = await update_post_analytics(session, current_user.id)
    return AnalyticsBulkUpdateOut(**data)


@app.get("/analytics/insights", response_model=PerformanceAnalyticsAIOut)
async def analytics_ai_insights(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """LLM analysis of post performance + copy (OpenAI when configured)."""
    rows = list(session.exec(select(Post).where(Post.user_id == current_user.id)).all())
    payload = posts_as_ai_payload(list(rows))
    result = await asyncio.to_thread(analyze_performance, payload)
    return PerformanceAnalyticsAIOut(
        insights=result.insights,
        mistakes=result.mistakes,
        recommendations=result.recommendations,
        next_post_ideas=result.next_post_ideas,
    )


@app.get("/stats")
async def stats(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Extended stats for dashboards (includes plan + breakdown fields)."""
    payload = get_analytics_payload(session, current_user.id)
    stmt = select(Post).where(Post.user_id == current_user.id)
    rows = session.exec(stmt).all()
    scheduled = sum(
        1
        for r in rows
        if r.status in ("approved", "publishing")
        or (
            r.status == "failed"
            and getattr(r, "next_publish_attempt_at", None) is not None
        )
    )
    pending = sum(1 for r in rows if r.status in ("review", "draft"))
    attempted = payload["posts_published"] + payload["posts_failed"]
    success_rate_pct = (
        round((payload["posts_published"] / attempted) * 100, 1) if attempted > 0 else None
    )
    return {
        "total_posts": payload["total_posts"],
        "published": payload["posts_published"],
        "failed": payload["posts_failed"],
        "scheduled": scheduled,
        "pending_review": pending,
        "success_rate_pct": success_rate_pct,
        "last_published_at": payload["last_published_at"],
        "total_campaigns": payload["total_campaigns"],
        "plan": current_user.plan,
    }


# ---------------------------------------------------------------------------
# Campaign workflow: submit for review
# ---------------------------------------------------------------------------

@app.post("/campaigns/{campaign_id}/submit-review")
def submit_campaign_for_review(
    campaign_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_permission("review_campaign")),
):
    """
    Transition a campaign from draft/generated → in_review.
    Any team member can submit; only owner/admin can approve.
    """
    camp = session.get(Campaign, campaign_id)
    user_team_id = getattr(current_user, "team_id", None)
    camp_team_id = getattr(camp, "team_id", None) if camp else None
    owns_campaign = camp and camp.user_id == current_user.id
    same_team = camp and camp_team_id is not None and camp_team_id == user_team_id
    if not camp or (not owns_campaign and not same_team):
        raise HTTPException(status_code=404, detail="Campaign not found")
    allowed_from = {"draft", "pending_approval", "generated"}
    if camp.status not in allowed_from:
        raise HTTPException(
            status_code=400,
            detail=f"Campaign cannot be submitted for review from status '{camp.status}'",
        )
    camp.status = "in_review"
    camp.updated_at = datetime.utcnow()
    session.add(camp)
    session.commit()
    return {"ok": True, "campaign_id": campaign_id, "status": "in_review"}


# ---------------------------------------------------------------------------
# Team management routes
# ---------------------------------------------------------------------------

@app.get("/teams/{team_id}", response_model=TeamOut)
def get_team(
    team_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Get team details. Requester must be a member."""
    team = session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    if getattr(current_user, "team_id", None) != team_id:
        raise HTTPException(status_code=403, detail="You are not a member of this team")
    return TeamOut(
        id=team.id,
        name=team.name,
        account_type=team.account_type,
        owner_id=team.owner_id,
        max_members=team.max_members,
        created_at=team.created_at,
    )


@app.get("/teams/{team_id}/members", response_model=List[MemberOut])
def get_team_members(
    team_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """List all members of a team."""
    members = list_members(session, team_id=team_id, requester=current_user)
    return [
        MemberOut(
            id=m.id,
            email=m.email,
            role=m.role,
            account_type=m.account_type,
            team_id=m.team_id,
        )
        for m in members
    ]


@app.post("/teams/{team_id}/invite", response_model=InviteOut)
def invite_team_member(
    team_id: int,
    body: InviteMemberRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Owner creates an invite link for *email*.
    Returns the invite record including the signup URL.
    No email is sent — caller is responsible for sharing the link.
    """
    # Derive base URL from the incoming request so the link works in all environments
    base_url = (
        os.getenv("BROKERAI_PUBLIC_ORIGIN", "").rstrip("/")
        or str(request.base_url).rstrip("/")
    )
    invite = create_invite(
        session,
        team_id=team_id,
        requester=current_user,
        email=str(body.email),
        base_url=base_url,
    )
    signup_url = f"{base_url}/signup.html?invite_token={invite.token}"
    log.info(
        "invite_created team_id=%s inviter_id=%s email=%s invite_id=%s",
        team_id, current_user.id, body.email, invite.id,
    )
    return InviteOut(
        id=invite.id,
        email=invite.email,
        team_id=invite.team_id,
        role=invite.role,
        token=invite.token,
        is_used=invite.is_used,
        expires_at=invite.expires_at,
        created_at=invite.created_at,
        signup_url=signup_url,
    )


@app.get("/invite-info", response_model=InviteLookupOut)
def invite_info(token: str, session: Session = Depends(get_session)):
    """
    Public endpoint — frontend calls this to pre-fill the signup form.
    Returns invite metadata (email, team_id) if the token is valid,
    or an error message if it is not.
    """
    from backend.services.invite_service import _utcnow
    invite = get_invite_by_token(session, token)
    if invite is None:
        return InviteLookupOut(valid=False, error="Invalid invite token")
    if invite.is_used:
        return InviteLookupOut(valid=False, error="This invite has already been used")
    if _utcnow() > invite.expires_at:
        return InviteLookupOut(valid=False, error="This invite has expired")
    return InviteLookupOut(valid=True, email=invite.email, team_id=invite.team_id)


@app.put("/teams/{team_id}/members/{user_id}", response_model=MemberOut)
def update_team_member_role(
    team_id: int,
    user_id: int,
    body: UpdateRoleRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Update a team member's role. Owner can assign admin; owner/admin can set member."""
    updated = update_member_role(
        session,
        team_id=team_id,
        requester=current_user,
        target_user_id=user_id,
        new_role=body.role,
    )
    return MemberOut(
        id=updated.id,
        email=updated.email,
        role=updated.role,
        account_type=updated.account_type,
        team_id=updated.team_id,
    )


@app.delete("/teams/{team_id}/members/{user_id}")
def remove_team_member(
    team_id: int,
    user_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Remove a member from the team. Owner/admin only. Cannot remove the owner."""
    remove_member(session, team_id=team_id, requester=current_user, target_user_id=user_id)
    return {"ok": True, "user_id": user_id, "removed": True}


# ---------------------------------------------------------------------------
# New Campaign Wizard (Phase 1) endpoints
# ---------------------------------------------------------------------------

async def _openai_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.7,
    max_retries: int = 2,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """Run an OpenAI chat completion with JSON mode.

    Hardened for production:
      - 503 on missing API key (clear message)
      - timeout on slow calls (default 30s)
      - automatic retries on transient errors (rate limits, timeouts, 5xx)
      - friendly HTTPException on final failure — never leaks a 500
      - always returns a dict (bad JSON yields {})
    """
    key = _openai_api_key()
    if not key:
        raise HTTPException(
            status_code=503,
            detail="AI generation is not configured. Please set OPENAI_API_KEY.",
        )

    # Lazy import so tests without openai installed don't crash at import.
    from openai import (
        APIConnectionError,
        APIError,
        APITimeoutError,
        AsyncOpenAI,
        AuthenticationError,
        BadRequestError,
        RateLimitError,
    )

    client = AsyncOpenAI(api_key=key, timeout=timeout)
    model = "gpt-4o-mini"

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        _t0 = time.perf_counter()
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=temperature,
            )
            raw = (resp.choices[0].message.content or "{}").strip()
            _dur_ms = int((time.perf_counter() - _t0) * 1000)
            usage = getattr(resp, "usage", None)
            log_event(
                "ai.openai_json",
                status="ok",
                model=model,
                attempt=attempt,
                duration_ms=_dur_ms,
                response_len=len(raw),
                prompt_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
                completion_tokens=getattr(usage, "completion_tokens", None) if usage else None,
                total_tokens=getattr(usage, "total_tokens", None) if usage else None,
            )
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                log.warning("openai returned non-JSON payload (len=%s)", len(raw))
                return {}
        except AuthenticationError as exc:
            log.error("openai auth failed: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="AI provider rejected the API key. Please check OPENAI_API_KEY.",
            )
        except BadRequestError as exc:
            # Non-retryable — bad prompt / model name / request shape.
            log.error("openai bad request: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="AI request was invalid. Please try rephrasing your prompt.",
            )
        except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
            last_exc = exc
            _dur_ms = int((time.perf_counter() - _t0) * 1000)
            log_event(
                "ai.openai_json",
                level=logging.WARNING,
                status="transient_error",
                model=model,
                attempt=attempt,
                duration_ms=_dur_ms,
                error=type(exc).__name__,
            )
            if attempt < max_retries:
                # Exponential backoff: 0.5s, 1s, 2s...
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            log.warning("openai transient failure after %s retries: %s", max_retries, exc)
            raise HTTPException(
                status_code=503,
                detail="AI service is busy. Please try again in a moment.",
            )
        except APIError as exc:
            # 5xx from OpenAI — retry once
            last_exc = exc
            if attempt < max_retries:
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            log.exception("openai API error")
            raise HTTPException(
                status_code=502,
                detail="AI generation failed. Please try again.",
            )
        except Exception as exc:  # noqa: BLE001 — defensive catch-all for the user path
            log.exception("openai unexpected error: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="AI generation failed unexpectedly. Please try again.",
            )

    # Should be unreachable, but just in case.
    log.error("openai exhausted retries: %s", last_exc)
    raise HTTPException(
        status_code=503,
        detail="AI service is unavailable. Please try again shortly.",
    )


@app.post("/campaigns/prefill-from-prompt", response_model=PrefillFromPromptResponse)
@limiter.limit("60/hour")
async def campaigns_prefill_from_prompt(
    request: Request,
    body: PrefillFromPromptRequest,
    current_user: User = Depends(get_current_user),
):
    """One-sentence prompt → structured campaign defaults the wizard can pre-fill."""
    system = (
        "You convert a one-sentence marketing prompt into structured JSON. "
        "Return ONLY JSON with keys: name (short campaign title, <=60 chars), "
        "objective (one sentence), target_audience (one sentence), "
        "tone (one of professional|friendly|playful|bold|luxury), "
        "platforms (array subset of [\"instagram\",\"facebook\",\"linkedin\"]), "
        "content_type (one of single_image|carousel|video|story|text), "
        "keywords (array of 3-6 short keywords)."
    )
    data = await _openai_json(system, f"Prompt: {body.prompt}")
    return PrefillFromPromptResponse(
        name=str(data.get("name") or "")[:80],
        objective=str(data.get("objective") or ""),
        target_audience=str(data.get("target_audience") or ""),
        tone=str(data.get("tone") or "professional"),
        platforms=[str(p) for p in (data.get("platforms") or []) if isinstance(p, str)],
        content_type=str(data.get("content_type") or "single_image"),
        keywords=[str(k) for k in (data.get("keywords") or []) if isinstance(k, str)],
    )


@app.post("/campaigns/captions", response_model=CaptionsResponse)
async def campaigns_captions(
    body: CaptionsRequest,
    current_user: User = Depends(get_current_user),
):
    """Generate N caption variations + a shared hashtag set for the selected platform/tone."""
    system = (
        "You are an expert real-estate social media copywriter. "
        "Return ONLY JSON with keys: captions (array of strings) and hashtags (array of 8-15 strings). "
        "Captions must match the requested tone and platform conventions. "
        "Instagram/Facebook: 1-3 short lines + emojis allowed. LinkedIn: professional, no emojis. "
        "Never include fabricated statistics."
    )
    user = json.dumps({
        "objective": body.objective,
        "audience": body.target_audience,
        "tone": body.tone,
        "platform": body.platform,
        "count": body.count,
        "extra": body.extra_context,
    })
    data = await _openai_json(system, user)
    caps = [str(c) for c in (data.get("captions") or []) if isinstance(c, str)]
    tags = [str(h) for h in (data.get("hashtags") or []) if isinstance(h, str)]
    return CaptionsResponse(captions=caps[: body.count], hashtags=tags[:15])


@app.post("/campaigns/hooks", response_model=HooksResponse)
async def campaigns_hooks(
    body: HooksRequest,
    current_user: User = Depends(get_current_user),
):
    """Generate N opening-line hooks (first 1-2 lines) to grab attention."""
    system = (
        "You are a viral-hook specialist for social posts. "
        "Return ONLY JSON with key `hooks`: array of short opening lines (each 6-14 words). "
        "Mix curiosity, question, contrarian, and bold-claim styles. No hashtags, no emojis."
    )
    user = json.dumps({
        "objective": body.objective,
        "audience": body.target_audience,
        "count": body.count,
    })
    data = await _openai_json(system, user)
    hooks = [str(h) for h in (data.get("hooks") or []) if isinstance(h, str)]
    return HooksResponse(hooks=hooks[: body.count])


@app.post("/campaigns/modify-caption", response_model=ModifyCaptionResponse)
async def campaigns_modify_caption(
    body: ModifyCaptionRequest,
    current_user: User = Depends(get_current_user),
):
    instructions = {
        "shorter": "Make it ~40% shorter without losing the key message.",
        "longer": "Expand with one more benefit-driven sentence.",
        "add_emojis": "Add 2-4 tasteful emojis at natural spots.",
        "remove_emojis": "Strip all emojis cleanly.",
        "add_hashtags": "Append 5-8 highly-relevant hashtags on a new line.",
        "more_professional": "Rewrite in a more professional, executive tone.",
        "more_playful": "Rewrite in a more playful, conversational tone.",
        "add_cta": "Add one clear call-to-action sentence at the end.",
        "rewrite": "Rewrite from scratch keeping the original intent.",
    }
    instr = instructions.get(body.modifier, "Rewrite slightly improved.")
    system = (
        "You are a social copy editor. Return ONLY JSON with key `caption` containing the revised caption. "
        f"Task: {instr} Platform: {body.platform}."
    )
    data = await _openai_json(system, f"Original caption:\n{body.caption}", temperature=0.5)
    return ModifyCaptionResponse(caption=str(data.get("caption") or body.caption))


@app.post("/campaigns/preview-captions", response_model=PreviewCaptionsResponse)
async def campaigns_preview_captions(
    body: PreviewCaptionsRequest,
    current_user: User = Depends(get_current_user),
):
    """Return 2-4 draft captions for wizard Step 3 preview (pre-generation).

    These are lightweight GPT-4o-mini outputs — they give the user a flavour
    of what the full campaign will look like before they hit Launch.
    """
    if not _openai_api_key():
        # Return static placeholder captions so the wizard still works without OpenAI
        placeholders = [
            f"Discover your dream home in {body.location or 'your area'} — expert guidance from listing to keys. 🏡 DM us to get started!",
            f"Looking to {body.goal or 'grow your business'} in {body.location or 'your market'}? We make it simple. Ask us how!",
            f"Your next chapter starts here. Serving {body.location or 'the local area'} with trusted expertise. #RealEstate #HomeGoals",
        ]
        return PreviewCaptionsResponse(captions=placeholders[: max(1, min(body.count, 5))])

    platform_hint = ", ".join(body.platforms) if body.platforms else "social media"
    count = max(1, min(body.count, 5))

    system = (
        "You are an expert social media copywriter specialised in real estate and local business marketing. "
        "Return ONLY valid JSON with a single key 'captions' containing an array of caption strings. "
        "Each caption must be punchy, platform-native, include 2-4 relevant emojis and 2-5 hashtags. "
        f"Tone: {body.tone}. Platform(s): {platform_hint}. "
        "No preamble, no markdown fences, just the JSON object."
    )
    user_msg = json.dumps({
        "industry": body.bucket.replace("_", " "),
        "persona": body.persona,
        "goal": body.goal,
        "location": body.location,
        "count": count,
    })
    data = await _openai_json(system, user_msg, temperature=0.75)
    raw = data.get("captions") or []
    captions = [str(c) for c in raw if isinstance(c, str)][:count]
    # Pad with fallback if model returned fewer than requested
    while len(captions) < count:
        captions.append(
            f"Helping clients in {body.location or 'your area'} achieve their {body.goal or 'goals'}. Reach out today!"
        )
    return PreviewCaptionsResponse(captions=captions)


@app.post("/campaigns/preview-score", response_model=PreviewScoreResponse)
async def campaigns_preview_score(
    body: PreviewScoreRequest,
    current_user: User = Depends(get_current_user),
):
    """LLM-based Predictive Performance Score (0-100) + reasons and suggestions."""
    system = (
        "You are a social media analyst. Score the post from 0-100 for expected engagement. "
        "Return ONLY JSON with keys: score (int 0-100), grade (A+|A|B+|B|C|D), "
        "reasons (array of 2-4 short strings) and suggestions (array of 2-4 short strings). "
        "Weigh: hook strength, specificity, CTA presence, platform fit, media presence, and length."
    )
    user = json.dumps({
        "caption": body.caption,
        "platforms": body.platforms,
        "has_media": body.has_media,
        "objective": body.objective,
        "audience": body.target_audience,
    })
    data = await _openai_json(system, user, temperature=0.3)
    try:
        score = int(max(0, min(100, int(data.get("score") or 0))))
    except (TypeError, ValueError):
        score = 0
    grade = str(data.get("grade") or "")
    if grade not in ("A+", "A", "B+", "B", "C", "D"):
        grade = "A+" if score >= 90 else "A" if score >= 80 else "B+" if score >= 70 else "B" if score >= 60 else "C" if score >= 45 else "D"
    return PreviewScoreResponse(
        score=score,
        grade=grade,
        reasons=[str(r) for r in (data.get("reasons") or []) if isinstance(r, str)][:4],
        suggestions=[str(s) for s in (data.get("suggestions") or []) if isinstance(s, str)][:4],
    )


@app.put("/me/brand-kit", response_model=UserOut)
def update_brand_kit(
    body: BrandKitUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Save or update the user's brand kit (logo, colors, font, voice, source)."""
    user = session.get(User, current_user.id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        if v is None:
            continue
        if hasattr(user, k):
            setattr(user, k, str(v))
    if not user.brand_source and any(changes.values()):
        user.brand_source = "upload"
    session.add(user)
    session.commit()
    session.refresh(user)
    return UserOut.model_validate(user)


@app.post("/me/brand-kit/generate", response_model=UserOut)
async def generate_brand_kit(
    body: BrandKitAIRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """AI-generate a starter brand kit (colors, font, voice). Logo stays empty unless uploaded."""
    system = (
        "You are a brand strategist. Return ONLY JSON with keys: "
        "primary_color (hex like #RRGGBB), secondary_color (hex), "
        "font (one of: Inter, Poppins, Montserrat, Playfair Display, Lato, Nunito), "
        "voice (one short descriptor of 4-10 words)."
    )
    user = json.dumps({
        "industry": body.industry,
        "vibe": body.vibe,
        "primary_color_hint": body.primary_color_hint,
    })
    data = await _openai_json(system, user, temperature=0.4)

    user_row = session.get(User, current_user.id)
    if not user_row:
        raise HTTPException(status_code=404, detail="User not found")
    user_row.brand_primary_color = str(data.get("primary_color") or user_row.brand_primary_color or "#0F62FE")
    user_row.brand_secondary_color = str(data.get("secondary_color") or user_row.brand_secondary_color or "#111827")
    user_row.brand_font = str(data.get("font") or user_row.brand_font or "Inter")
    user_row.brand_voice = str(data.get("voice") or user_row.brand_voice or "professional and approachable")
    user_row.brand_source = "ai"
    session.add(user_row)
    session.commit()
    session.refresh(user_row)
    return UserOut.model_validate(user_row)


@app.get("/unsplash/search", response_model=UnsplashSearchResponse)
async def unsplash_search(
    q: str,
    per_page: int = 12,
    orientation: str = "landscape",
    current_user: User = Depends(get_current_user),
):
    """Proxy to Unsplash search (keeps the access key server-side)."""
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query 'q' is required")
    try:
        photos, total = await unsplash_search_photos(q, per_page=per_page, orientation=orientation)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return UnsplashSearchResponse(photos=photos, total=total)  # type: ignore[arg-type]


@app.post("/video/generate", response_model=VideoGenerateResponse)
async def video_generate(
    body: VideoGenerateRequest,
    current_user: User = Depends(get_current_user),
):
    """Generate an AI video via Replicate. Returns video_url if it finishes within ~25s, else prediction_id to poll."""
    result = await create_video_prediction(
        body.prompt, body.duration_seconds, body.aspect_ratio
    )
    if not result.get("ok") and result.get("status") not in ("queued", "starting", "processing"):
        # Hard failure (no token, 4xx, etc.)
        return VideoGenerateResponse(
            status="failed",
            video_url="",
            prediction_id=str(result.get("prediction_id") or ""),
            error=str(result.get("error") or "video_generation_failed"),
        )
    return VideoGenerateResponse(
        status=result.get("status") or "queued",
        video_url=str(result.get("video_url") or ""),
        prediction_id=str(result.get("prediction_id") or ""),
        error=str(result.get("error") or ""),
    )


@app.get("/video/status/{prediction_id}", response_model=VideoGenerateResponse)
async def video_status(
    prediction_id: str,
    current_user: User = Depends(get_current_user),
):
    result = await fetch_video_prediction(prediction_id)
    return VideoGenerateResponse(
        status=result.get("status") or "queued",
        video_url=str(result.get("video_url") or ""),
        prediction_id=str(result.get("prediction_id") or prediction_id),
        error=str(result.get("error") or ""),
    )


@app.post("/assistant/chat", response_model=AssistantChatResponse)
async def assistant_chat(
    body: AssistantChatRequest,
    current_user: User = Depends(get_current_user),
):
    """AI Campaign Assistant — reads current wizard state + user message, returns
    a conversational reply plus a structured patch the frontend applies to the wizard.
    """
    if not _openai_api_key():
        return AssistantChatResponse(
            reply="AI assistant is offline — OPENAI_API_KEY is not configured.",
            suggestions=[],
        )

    state = body.state.model_dump()
    history = [{"role": m.role, "content": m.content} for m in body.history[-8:]]

    system = (
        "You are BrokerAI's Campaign Assistant, embedded in a campaign-building wizard for "
        "real estate brokers and agents. The user is mid-flow building a social campaign. "
        "You can both (a) reply conversationally and (b) propose structured edits to the wizard. "
        "Return JSON with keys: reply (string, <=120 words, friendly & concrete), "
        "patch (object with any subset of: name, goal, prompt, content_type, tone, platforms[], "
        "location, start_date (YYYY-MM-DD), duration ('single'|'7d'|'14d'|'30d'), selected_caption, "
        "captions_append[], hooks_append[]), suggestions (array of 2-4 short follow-up chips). "
        "Only include fields in 'patch' that you actually want to change — omit the rest. "
        "content_type must be one of: image, carousel, video, reel, story. "
        "tone must be one of: professional, friendly, playful, bold, luxury. "
        "platforms entries must be one of: instagram, facebook, linkedin, twitter, tiktok, youtube. "
        "Keep all text compliant with real-estate advertising rules (no guaranteed returns, no "
        "discriminatory language). If the user asks for captions or hooks, put them in "
        "captions_append / hooks_append. Never invent fields."
    )

    user_payload = (
        "CURRENT WIZARD STATE:\n"
        + json.dumps(state, ensure_ascii=False)
        + "\n\nCONVERSATION SO FAR:\n"
        + (json.dumps(history, ensure_ascii=False) if history else "[]")
        + "\n\nUSER MESSAGE:\n"
        + body.message.strip()
    )

    try:
        data = await _openai_json(system=system, user=user_payload, temperature=0.5)
    except Exception as e:  # noqa: BLE001
        log.warning("assistant_chat_openai_failed: %s", e)
        return AssistantChatResponse(
            reply="Sorry — I hit a snag talking to the AI. Try again in a moment.",
            suggestions=[],
        )

    reply = str(data.get("reply") or "").strip() or "Okay."
    raw_patch = data.get("patch") or {}
    if not isinstance(raw_patch, dict):
        raw_patch = {}

    # Light normalization + whitelist — Pydantic will drop unknown keys.
    allowed = {
        "name", "goal", "prompt", "content_type", "tone", "platforms",
        "location", "start_date", "duration", "selected_caption",
        "captions_append", "hooks_append",
    }
    clean_patch: Dict[str, Any] = {k: v for k, v in raw_patch.items() if k in allowed and v is not None}

    if "platforms" in clean_patch and not isinstance(clean_patch["platforms"], list):
        clean_patch.pop("platforms", None)
    for k in ("captions_append", "hooks_append"):
        if k in clean_patch and not isinstance(clean_patch[k], list):
            clean_patch.pop(k, None)

    raw_suggestions = data.get("suggestions") or []
    suggestions = [str(s).strip() for s in raw_suggestions if isinstance(s, (str, int))][:4]

    try:
        patch_obj = AssistantPatch(**clean_patch)
    except Exception:
        patch_obj = AssistantPatch()

    return AssistantChatResponse(reply=reply, patch=patch_obj, suggestions=suggestions)


# ---------- Campaign Templates (save / list / load / delete) ----------

def _template_to_out(t: CampaignTemplate) -> TemplateOut:
    try:
        payload = json.loads(t.payload or "{}")
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    return TemplateOut(
        id=t.id,
        name=t.name or "",
        description=t.description or "",
        payload=payload,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _brand_asset_to_out(row: BrandAsset) -> BrandAssetOut:
    return BrandAssetOut(
        id=int(row.id or 0),
        user_id=int(row.user_id or 0),
        kind=row.kind or "document",
        original_filename=row.original_filename or "",
        content_type=row.content_type or "application/octet-stream",
        size_bytes=int(row.size_bytes or 0),
        created_at=row.created_at,
    )


@app.post("/brand-assets/upload", response_model=BrandAssetOut)
async def upload_brand_asset(
    file: UploadFile = File(...),
    kind: str = Form("document"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Store a brand file (logo, template, guidelines). Allowed: png, jpg, webp, gif, pdf, docx."""
    raw = await file.read()
    size = len(raw)
    try:
        ext = validate_upload(file.filename or "upload", size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    stored_name = new_stored_filename(ext)
    out_dir = brand_dir_for_user(BASE_DIR, int(current_user.id or 0))
    dest = out_dir / stored_name
    ct = file.content_type or guess_content_type(ext)
    if not ct or ct == "application/octet-stream":
        ct = guess_content_type(ext)

    try:
        dest.write_bytes(raw)
    except OSError:
        raise HTTPException(status_code=500, detail="Could not save file") from None

    row = BrandAsset(
        user_id=int(current_user.id or 0),
        kind=normalize_kind(kind),
        original_filename=(file.filename or stored_name)[:512],
        stored_filename=stored_name,
        content_type=(ct or guess_content_type(ext))[:255],
        size_bytes=size,
    )
    try:
        session.add(row)
        session.commit()
        session.refresh(row)
    except Exception:
        session.rollback()
        try:
            if dest.is_file():
                dest.unlink()
        except OSError:
            pass
        raise
    return _brand_asset_to_out(row)


@app.get("/brand-assets", response_model=BrandAssetListResponse)
def list_brand_assets(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    stmt = (
        select(BrandAsset)
        .where(BrandAsset.user_id == current_user.id)
        .order_by(BrandAsset.created_at.desc())
    )
    rows = list(session.exec(stmt).all())
    return BrandAssetListResponse(items=[_brand_asset_to_out(r) for r in rows])


@app.get("/brand-assets/{asset_id}/file")
def download_brand_asset(
    asset_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    row = session.get(BrandAsset, asset_id)
    if not row or int(row.user_id) != int(current_user.id or 0):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        path = disk_path(BASE_DIR, int(current_user.id or 0), row.stored_filename or "")
    except ValueError:
        raise HTTPException(status_code=404, detail="File not found") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File missing on disk")
    return FileResponse(
        path,
        media_type=row.content_type or "application/octet-stream",
        filename=row.original_filename or path.name,
    )


@app.delete("/brand-assets/{asset_id}")
def delete_brand_asset(
    asset_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    row = session.get(BrandAsset, asset_id)
    if not row or int(row.user_id) != int(current_user.id or 0):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        path = disk_path(BASE_DIR, int(current_user.id or 0), row.stored_filename or "")
        if path.is_file():
            path.unlink()
    except OSError:
        pass
    session.delete(row)
    session.commit()
    return {"ok": True, "deleted": asset_id}


@app.post("/templates", response_model=TemplateOut)
def create_template(
    body: TemplateCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    tpl = CampaignTemplate(
        user_id=current_user.id,
        team_id=getattr(current_user, "team_id", None),
        name=body.name.strip() or "Untitled Template",
        description=(body.description or "").strip(),
        payload=json.dumps(body.payload or {}, ensure_ascii=False),
    )
    session.add(tpl)
    session.commit()
    session.refresh(tpl)
    return _template_to_out(tpl)


@app.get("/templates", response_model=TemplateListResponse)
def list_templates(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    stmt = (
        select(CampaignTemplate)
        .where(CampaignTemplate.user_id == current_user.id)
        .order_by(CampaignTemplate.updated_at.desc())
    )
    rows = list(session.exec(stmt).all())
    return TemplateListResponse(items=[_template_to_out(r) for r in rows], total=len(rows))


@app.get("/templates/{template_id}", response_model=TemplateOut)
def get_template(
    template_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    tpl = session.get(CampaignTemplate, template_id)
    if not tpl or tpl.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Template not found")
    return _template_to_out(tpl)


@app.put("/templates/{template_id}", response_model=TemplateOut)
def update_template(
    template_id: int,
    body: TemplateUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    tpl = session.get(CampaignTemplate, template_id)
    if not tpl or tpl.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Template not found")
    if body.name is not None:
        tpl.name = body.name.strip() or tpl.name
    if body.description is not None:
        tpl.description = body.description.strip()
    if body.payload is not None:
        tpl.payload = json.dumps(body.payload, ensure_ascii=False)
    tpl.updated_at = datetime.utcnow()
    session.add(tpl)
    session.commit()
    session.refresh(tpl)
    return _template_to_out(tpl)


@app.delete("/templates/{template_id}")
def delete_template(
    template_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    tpl = session.get(CampaignTemplate, template_id)
    if not tpl or tpl.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Template not found")
    session.delete(tpl)
    session.commit()
    return {"ok": True, "deleted": template_id}


# ---------- Duplicate Campaign ----------

@app.post("/campaigns/{campaign_id}/duplicate", response_model=CampaignOut)
def duplicate_campaign(
    campaign_id: int,
    body: DuplicateCampaignRequest = DuplicateCampaignRequest(),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    src = session.get(Campaign, campaign_id)
    if not src or src.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Campaign not found")

    new_name = (body.name or "").strip() or f"{src.name or 'Campaign'} (Copy)"
    copy = Campaign(
        user_id=current_user.id,
        team_id=src.team_id,
        created_by=current_user.id,
        approved_by=None,
        status="draft",
        graph_thread_id="",
        name=new_name[:200],
        objective=src.objective or "",
        target_audience=src.target_audience or "",
        facebook_url=src.facebook_url or "",
        instagram_url=src.instagram_url or "",
        linkedin_url=src.linkedin_url or "",
    )
    session.add(copy)
    session.commit()
    session.refresh(copy)

    if body.include_posts:
        src_posts = list(session.exec(select(Post).where(Post.campaign_id == src.id)).all())
        for p in src_posts:
            dup = Post(
                user_id=current_user.id,
                campaign_id=copy.id,
                platform=p.platform or "",
                social_post_id="",
                content=p.content or "",
                publish_platforms=list(p.publish_platforms or []),
                status="draft",
                scheduled_at=None,
                published_at=None,
                platform_response="{}",
                compliance_passed=None,
                compliance_issues="[]",
            )
            session.add(dup)
        session.commit()

    return CampaignOut.model_validate(copy)


# ---------- Lead Forms & Leads ----------

def _lf_public_slug() -> str:
    import secrets
    return secrets.token_urlsafe(8).replace("_", "").replace("-", "")[:10]


def _parse_fields(raw: str) -> List[Dict[str, Any]]:
    try:
        arr = json.loads(raw or "[]")
        return arr if isinstance(arr, list) else []
    except Exception:
        return []


def _leadform_to_out(lf: LeadForm, *, lead_count: int = 0) -> LeadFormOut:
    fields_raw = _parse_fields(lf.fields)
    # Re-validate to LeadFormField; drop malformed entries defensively
    fields: List[LeadFormField] = []
    for f in fields_raw:
        try:
            fields.append(LeadFormField(**f))
        except Exception:
            continue
    return LeadFormOut(
        id=lf.id,
        name=lf.name or "",
        headline=lf.headline or "",
        description=lf.description or "",
        fields=fields,
        thank_you_message=lf.thank_you_message or "",
        redirect_url=lf.redirect_url or "",
        public_slug=lf.public_slug or "",
        public_url=f"/lead/{lf.public_slug}" if lf.public_slug else "",
        is_active=bool(lf.is_active),
        lead_count=lead_count,
        created_at=lf.created_at,
        updated_at=lf.updated_at,
    )


@app.post("/lead-forms", response_model=LeadFormOut)
def create_lead_form(
    body: LeadFormCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    # Dedup field keys
    seen = set()
    unique_fields: List[LeadFormField] = []
    for f in body.fields:
        if f.key in seen:
            continue
        seen.add(f.key)
        unique_fields.append(f)
    if not unique_fields:
        # Provide a sensible default form if user didn't specify fields.
        unique_fields = [
            LeadFormField(key="name", label="Full name", type="text", required=True),
            LeadFormField(key="email", label="Email", type="email", required=True),
            LeadFormField(key="phone", label="Phone", type="phone", required=False),
        ]

    lf = LeadForm(
        user_id=current_user.id,
        team_id=getattr(current_user, "team_id", None),
        name=body.name.strip(),
        headline=body.headline.strip(),
        description=body.description.strip(),
        fields=json.dumps([f.model_dump() for f in unique_fields], ensure_ascii=False),
        thank_you_message=body.thank_you_message.strip() or "Thanks! We'll be in touch soon.",
        redirect_url=body.redirect_url.strip(),
        public_slug=_lf_public_slug(),
        is_active=body.is_active,
    )
    session.add(lf)
    session.commit()
    session.refresh(lf)
    return _leadform_to_out(lf, lead_count=0)


@app.get("/lead-forms", response_model=LeadFormListResponse)
def list_lead_forms(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    stmt = (
        select(LeadForm)
        .where(LeadForm.user_id == current_user.id)
        .order_by(LeadForm.updated_at.desc())
    )
    rows = list(session.exec(stmt).all())
    out: List[LeadFormOut] = []
    for lf in rows:
        count = len(list(session.exec(select(Lead).where(Lead.form_id == lf.id)).all()))
        out.append(_leadform_to_out(lf, lead_count=count))
    return LeadFormListResponse(items=out, total=len(out))


@app.get("/lead-forms/{form_id}", response_model=LeadFormOut)
def get_lead_form(
    form_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    lf = session.get(LeadForm, form_id)
    if not lf or lf.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Lead form not found")
    count = len(list(session.exec(select(Lead).where(Lead.form_id == lf.id)).all()))
    return _leadform_to_out(lf, lead_count=count)


@app.put("/lead-forms/{form_id}", response_model=LeadFormOut)
def update_lead_form(
    form_id: int,
    body: LeadFormUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    lf = session.get(LeadForm, form_id)
    if not lf or lf.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Lead form not found")
    if body.name is not None:
        lf.name = body.name.strip() or lf.name
    if body.headline is not None:
        lf.headline = body.headline.strip()
    if body.description is not None:
        lf.description = body.description.strip()
    if body.fields is not None:
        seen = set()
        unique: List[LeadFormField] = []
        for f in body.fields:
            if f.key in seen:
                continue
            seen.add(f.key); unique.append(f)
        lf.fields = json.dumps([f.model_dump() for f in unique], ensure_ascii=False)
    if body.thank_you_message is not None:
        lf.thank_you_message = body.thank_you_message.strip() or lf.thank_you_message
    if body.redirect_url is not None:
        lf.redirect_url = body.redirect_url.strip()
    if body.is_active is not None:
        lf.is_active = bool(body.is_active)
    lf.updated_at = datetime.utcnow()
    session.add(lf)
    session.commit()
    session.refresh(lf)
    count = len(list(session.exec(select(Lead).where(Lead.form_id == lf.id)).all()))
    return _leadform_to_out(lf, lead_count=count)


@app.delete("/lead-forms/{form_id}")
def delete_lead_form(
    form_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    lf = session.get(LeadForm, form_id)
    if not lf or lf.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Lead form not found")
    # Detach from any campaigns that reference this form
    linked = list(session.exec(select(Campaign).where(Campaign.lead_form_id == form_id)).all())
    for c in linked:
        c.lead_form_id = None
        session.add(c)
    # Delete leads + form
    leads = list(session.exec(select(Lead).where(Lead.form_id == form_id)).all())
    for l in leads:
        session.delete(l)
    session.delete(lf)
    session.commit()
    return {"ok": True, "deleted": form_id, "detached_campaigns": [c.id for c in linked]}


@app.get("/public/lead-forms/{slug}", response_model=LeadFormOut)
def public_get_lead_form(slug: str, session: Session = Depends(get_session)):
    """Unauthenticated read — used by the public form renderer page."""
    lf = session.exec(select(LeadForm).where(LeadForm.public_slug == slug)).first()
    if not lf or not lf.is_active:
        raise HTTPException(status_code=404, detail="Form not found")
    return _leadform_to_out(lf)


@app.post("/public/lead-forms/{slug}/submit", response_model=LeadSubmitResponse)
def public_submit_lead_form(
    slug: str,
    body: LeadSubmitRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    lf = session.exec(select(LeadForm).where(LeadForm.public_slug == slug)).first()
    if not lf or not lf.is_active:
        raise HTTPException(status_code=404, detail="Form not found")

    spec_fields = _parse_fields(lf.fields)
    # Validate: every required field must be present and non-empty
    cleaned: Dict[str, Any] = {}
    for raw in spec_fields:
        try:
            fld = LeadFormField(**raw)
        except Exception:
            continue
        val = body.data.get(fld.key)
        if fld.required and (val is None or (isinstance(val, str) and not val.strip())):
            raise HTTPException(status_code=400, detail=f"Missing required field: {fld.label}")
        if val is not None:
            # Clip outrageously long strings to protect the DB
            if isinstance(val, str) and len(val) > 2000:
                val = val[:2000]
            cleaned[fld.key] = val

    # Reject submissions with zero captured fields
    if not cleaned:
        raise HTTPException(status_code=400, detail="No valid fields submitted")

    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")[:500]

    lead = Lead(
        form_id=lf.id,
        user_id=lf.user_id,
        data=json.dumps(cleaned, ensure_ascii=False),
        source=(body.source or "")[:120],
        utm_campaign=(body.utm_campaign or "")[:120],
        utm_source=(body.utm_source or "")[:120],
        ip=ip,
        user_agent=ua,
    )
    session.add(lead)
    session.commit()
    session.refresh(lead)

    return LeadSubmitResponse(
        ok=True,
        lead_id=lead.id,
        thank_you_message=lf.thank_you_message or "Thanks! We'll be in touch soon.",
        redirect_url=lf.redirect_url or "",
    )


def _lead_to_out(l: Lead) -> LeadOut:
    try:
        data = json.loads(l.data or "{}")
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    return LeadOut(
        id=l.id,
        form_id=l.form_id,
        data=data,
        source=l.source or "",
        utm_campaign=l.utm_campaign or "",
        utm_source=l.utm_source or "",
        captured_at=l.captured_at,
    )


@app.get("/lead-forms/{form_id}/leads", response_model=LeadListResponse)
def list_form_leads(
    form_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    lf = session.get(LeadForm, form_id)
    if not lf or lf.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Lead form not found")
    rows = list(session.exec(
        select(Lead).where(Lead.form_id == form_id).order_by(Lead.captured_at.desc())
    ).all())
    return LeadListResponse(items=[_lead_to_out(r) for r in rows], total=len(rows))


@app.get("/lead-forms/{form_id}/leads.csv", response_class=PlainTextResponse)
def export_form_leads_csv(
    form_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    import csv, io
    lf = session.get(LeadForm, form_id)
    if not lf or lf.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Lead form not found")
    spec = [LeadFormField(**f) for f in _parse_fields(lf.fields) if isinstance(f, dict)]
    rows = list(session.exec(
        select(Lead).where(Lead.form_id == form_id).order_by(Lead.captured_at.asc())
    ).all())
    buf = io.StringIO()
    writer = csv.writer(buf)
    header = ["id", "captured_at", "source", "utm_campaign", "utm_source"] + [f.key for f in spec]
    writer.writerow(header)
    for r in rows:
        try:
            data = json.loads(r.data or "{}") or {}
        except Exception:
            data = {}
        writer.writerow(
            [r.id, r.captured_at.isoformat(), r.source or "", r.utm_campaign or "", r.utm_source or ""]
            + [str(data.get(f.key, "")) for f in spec]
        )
    return PlainTextResponse(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="leads_form_{form_id}.csv"'},
    )


@app.post("/campaigns/{campaign_id}/lead-form", response_model=CampaignOut)
def attach_lead_form(
    campaign_id: int,
    body: AttachLeadFormRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    camp = session.get(Campaign, campaign_id)
    if not camp or camp.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if body.lead_form_id is not None:
        lf = session.get(LeadForm, body.lead_form_id)
        if not lf or lf.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Lead form not found")
        camp.lead_form_id = lf.id
    else:
        camp.lead_form_id = None
    camp.updated_at = datetime.utcnow()
    session.add(camp)
    session.commit()
    session.refresh(camp)
    return CampaignOut.model_validate(camp)


@app.get("/lead/{slug}", response_class=FileResponse)
def public_lead_form_page(slug: str):
    """Serve the public-facing form renderer page (no auth).
    Accepts both `/lead/abc123` and `/lead/abc123.html`.
    """
    return FileResponse("frontend/lead-form-public.html")


@app.get("/leads.html", response_class=FileResponse)
def leads_admin_page():
    return FileResponse("frontend/leads.html")


@app.get("/lead-forms.html", response_class=FileResponse)
def lead_forms_admin_page():
    return FileResponse("frontend/lead-forms.html")


# =========================================================================
# Phase 2 #4 — Comment-to-DM automations
# =========================================================================

def _automation_to_out(a: "CommentAutomation") -> CommentAutomationOut:
    try:
        kws = json.loads(a.keywords) if a.keywords else []
        if not isinstance(kws, list):
            kws = []
    except Exception:
        kws = []
    return CommentAutomationOut(
        id=a.id, user_id=a.user_id, name=a.name,
        post_id=a.post_id, platform=a.platform, external_post_id=a.external_post_id or "",
        keywords=[str(k) for k in kws],
        match_mode=a.match_mode or "any", case_sensitive=bool(a.case_sensitive),
        reply_comment_enabled=bool(a.reply_comment_enabled),
        reply_comment_template=a.reply_comment_template or "",
        dm_enabled=bool(a.dm_enabled), dm_template=a.dm_template or "",
        lead_form_id=a.lead_form_id, link_url=a.link_url or "",
        is_active=bool(a.is_active), trigger_count=int(a.trigger_count or 0),
        last_triggered_at=a.last_triggered_at,
        created_at=a.created_at, updated_at=a.updated_at,
    )


def _match_comment(text: str, keywords: list[str], mode: str, case_sensitive: bool) -> tuple[bool, str]:
    """Return (matched, keyword_that_matched)."""
    if not text or not keywords:
        return False, ""
    haystack = text if case_sensitive else text.lower()
    kws = [k if case_sensitive else k.lower() for k in keywords if k]
    if not kws:
        return False, ""
    if mode == "exact":
        for k in kws:
            if haystack.strip() == k.strip():
                return True, k
        return False, ""
    if mode == "all":
        if all(k in haystack for k in kws):
            return True, ", ".join(kws)
        return False, ""
    # any
    for k in kws:
        if k in haystack:
            return True, k
    return False, ""


def _render_link(aut: CommentAutomation, session: Session, request: Request) -> str:
    """Resolve {link} for DM template: prefer explicit link_url, else lead-form public URL."""
    if (aut.link_url or "").strip():
        return aut.link_url.strip()
    if aut.lead_form_id:
        lf = session.get(LeadForm, aut.lead_form_id)
        if lf and lf.public_slug:
            base = str(request.base_url).rstrip("/")
            return f"{base}/lead/{lf.public_slug}"
    return ""


def _render_template(tmpl: str, *, handle: str, link: str, keyword: str, comment: str) -> str:
    return (tmpl or "").format(
        handle=handle or "there",
        link=link or "",
        keyword=keyword or "",
        comment=(comment or "")[:200],
    )


@app.post("/comment-automations", response_model=CommentAutomationOut)
def create_comment_automation(
    body: CommentAutomationCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CommentAutomationOut:
    if body.post_id is not None:
        p = session.get(Post, body.post_id)
        if not p:
            raise HTTPException(status_code=404, detail="Post not found")
        # ownership via campaign
        c = session.get(Campaign, p.campaign_id)
        if not c or c.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not allowed")
    if body.lead_form_id is not None:
        lf = session.get(LeadForm, body.lead_form_id)
        if not lf or lf.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Lead form not found")
    a = CommentAutomation(
        user_id=current_user.id,
        name=body.name.strip(),
        post_id=body.post_id,
        platform=(body.platform or "instagram").strip().lower(),
        external_post_id=(body.external_post_id or "").strip(),
        keywords=json.dumps([k.strip() for k in body.keywords]),
        match_mode=body.match_mode,
        case_sensitive=body.case_sensitive,
        reply_comment_enabled=body.reply_comment_enabled,
        reply_comment_template=body.reply_comment_template,
        dm_enabled=body.dm_enabled,
        dm_template=body.dm_template,
        lead_form_id=body.lead_form_id,
        link_url=(body.link_url or "").strip(),
        is_active=body.is_active,
    )
    session.add(a)
    session.commit()
    session.refresh(a)
    return _automation_to_out(a)


@app.get("/comment-automations", response_model=CommentAutomationListResponse)
def list_comment_automations(
    post_id: Optional[int] = None,
    is_active: Optional[bool] = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CommentAutomationListResponse:
    q = select(CommentAutomation).where(CommentAutomation.user_id == current_user.id)
    if post_id is not None:
        q = q.where(CommentAutomation.post_id == post_id)
    if is_active is not None:
        q = q.where(CommentAutomation.is_active == is_active)
    rows = list(session.exec(q).all())
    rows.sort(key=lambda a: a.created_at, reverse=True)
    return CommentAutomationListResponse(
        items=[_automation_to_out(a) for a in rows],
        total=len(rows),
    )


@app.get("/comment-automations/{automation_id}", response_model=CommentAutomationOut)
def get_comment_automation(
    automation_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CommentAutomationOut:
    a = session.get(CommentAutomation, automation_id)
    if not a or a.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Automation not found")
    return _automation_to_out(a)


@app.put("/comment-automations/{automation_id}", response_model=CommentAutomationOut)
def update_comment_automation(
    automation_id: int,
    body: CommentAutomationUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CommentAutomationOut:
    a = session.get(CommentAutomation, automation_id)
    if not a or a.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Automation not found")
    data = body.model_dump(exclude_unset=True)
    if "keywords" in data and data["keywords"] is not None:
        kws = [(k or "").strip() for k in data["keywords"] if (k or "").strip()]
        if not kws:
            raise HTTPException(status_code=400, detail="At least one keyword is required")
        a.keywords = json.dumps(kws[:20])
    if "post_id" in data:
        pid = data["post_id"]
        if pid is not None:
            p = session.get(Post, pid)
            if not p:
                raise HTTPException(status_code=404, detail="Post not found")
            c = session.get(Campaign, p.campaign_id)
            if not c or c.user_id != current_user.id:
                raise HTTPException(status_code=403, detail="Not allowed")
        a.post_id = pid
    if "lead_form_id" in data:
        lfid = data["lead_form_id"]
        if lfid is not None:
            lf = session.get(LeadForm, lfid)
            if not lf or lf.user_id != current_user.id:
                raise HTTPException(status_code=404, detail="Lead form not found")
        a.lead_form_id = lfid
    for f in (
        "name", "platform", "external_post_id", "match_mode", "case_sensitive",
        "reply_comment_enabled", "reply_comment_template",
        "dm_enabled", "dm_template", "link_url", "is_active",
    ):
        if f in data and data[f] is not None:
            setattr(a, f, data[f])
    a.updated_at = datetime.utcnow()
    session.add(a); session.commit(); session.refresh(a)
    return _automation_to_out(a)


@app.delete("/comment-automations/{automation_id}")
def delete_comment_automation(
    automation_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    a = session.get(CommentAutomation, automation_id)
    if not a or a.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Automation not found")
    # Clean up triggers
    for t in session.exec(select(CommentTrigger).where(CommentTrigger.automation_id == automation_id)).all():
        session.delete(t)
    session.delete(a)
    session.commit()
    return {"ok": True, "deleted_id": automation_id}


@app.post("/comment-automations/{automation_id}/simulate", response_model=CommentSimulateResponse)
def simulate_comment_automation(
    automation_id: int,
    body: CommentSimulateRequest,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CommentSimulateResponse:
    a = session.get(CommentAutomation, automation_id)
    if not a or a.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Automation not found")
    try:
        kws = json.loads(a.keywords) if a.keywords else []
    except Exception:
        kws = []
    matched, mk = _match_comment(body.comment_text, kws, a.match_mode, a.case_sensitive)
    resp = CommentSimulateResponse(matched=matched, matched_keyword=mk)
    if not matched:
        return resp
    link = _render_link(a, session, request)
    resp.rendered_reply = _render_template(
        a.reply_comment_template, handle=body.commenter_handle, link=link,
        keyword=mk, comment=body.comment_text,
    ) if a.reply_comment_enabled else ""
    resp.rendered_dm = _render_template(
        a.dm_template, handle=body.commenter_handle, link=link,
        keyword=mk, comment=body.comment_text,
    ) if a.dm_enabled else ""
    if body.execute and a.is_active:
        # Stub send: we log but don't actually call Ayrshare yet. Marked as sent
        # unless templates are empty.
        reply_sent = bool(resp.rendered_reply)
        dm_sent = bool(resp.rendered_dm)
        trig = CommentTrigger(
            automation_id=a.id, user_id=a.user_id,
            commenter_handle=body.commenter_handle or "",
            commenter_id=body.commenter_id or "",
            comment_text=body.comment_text[:2000],
            external_comment_id=body.external_comment_id or "",
            matched_keyword=mk,
            reply_sent=reply_sent, dm_sent=dm_sent,
            reply_error="" if reply_sent else ("disabled" if not a.reply_comment_enabled else "no_template"),
            dm_error="" if dm_sent else ("disabled" if not a.dm_enabled else "no_template"),
        )
        session.add(trig)
        a.trigger_count = int(a.trigger_count or 0) + 1
        a.last_triggered_at = datetime.utcnow()
        session.add(a); session.commit(); session.refresh(trig)
        resp.trigger_id = trig.id
        resp.reply_sent = reply_sent
        resp.dm_sent = dm_sent
    return resp


@app.get("/comment-automations/{automation_id}/triggers", response_model=CommentTriggerListResponse)
def list_automation_triggers(
    automation_id: int,
    limit: int = 100,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CommentTriggerListResponse:
    a = session.get(CommentAutomation, automation_id)
    if not a or a.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Automation not found")
    q = select(CommentTrigger).where(CommentTrigger.automation_id == automation_id)
    rows = list(session.exec(q).all())
    rows.sort(key=lambda t: t.triggered_at, reverse=True)
    rows = rows[: max(1, min(limit, 500))]
    return CommentTriggerListResponse(
        items=[CommentTriggerOut.model_validate(t) for t in rows],
        total=len(rows),
    )


@app.post("/webhooks/comments", response_model=CommentSimulateResponse)
async def comment_webhook(
    payload: CommentWebhookPayload,
    request: Request,
    session: Session = Depends(get_session),
) -> CommentSimulateResponse:
    """Public-ish webhook endpoint for comment events from Ayrshare / Meta.

    Security note: in production, validate a shared secret header. For now
    we only match automations that have `external_post_id` set, so an
    attacker can't trigger automations they don't know the ID of.
    """
    secret_header = request.headers.get("X-BrokerAI-Webhook-Secret", "")
    expected = (os.getenv("WEBHOOK_SECRET") or "").strip()
    if expected and secret_header != expected:
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    if not payload.external_post_id or not payload.comment_text:
        return CommentSimulateResponse(matched=False)

    q = select(CommentAutomation).where(
        CommentAutomation.is_active == True,  # noqa: E712
        CommentAutomation.external_post_id == payload.external_post_id.strip(),
        CommentAutomation.platform == (payload.platform or "instagram").strip().lower(),
    )
    rows = list(session.exec(q).all())
    if not rows:
        return CommentSimulateResponse(matched=False)

    for a in rows:
        try:
            kws = json.loads(a.keywords) if a.keywords else []
        except Exception:
            kws = []
        matched, mk = _match_comment(payload.comment_text, kws, a.match_mode, a.case_sensitive)
        if not matched:
            continue
        link = _render_link(a, session, request)
        rendered_reply = _render_template(
            a.reply_comment_template, handle=payload.commenter_handle, link=link,
            keyword=mk, comment=payload.comment_text,
        ) if a.reply_comment_enabled else ""
        rendered_dm = _render_template(
            a.dm_template, handle=payload.commenter_handle, link=link,
            keyword=mk, comment=payload.comment_text,
        ) if a.dm_enabled else ""
        trig = CommentTrigger(
            automation_id=a.id, user_id=a.user_id,
            commenter_handle=payload.commenter_handle or "",
            commenter_id=payload.commenter_id or "",
            comment_text=payload.comment_text[:2000],
            external_comment_id=payload.external_comment_id or "",
            matched_keyword=mk,
            reply_sent=bool(rendered_reply), dm_sent=bool(rendered_dm),
            reply_error="" if rendered_reply else ("disabled" if not a.reply_comment_enabled else "no_template"),
            dm_error="" if rendered_dm else ("disabled" if not a.dm_enabled else "no_template"),
        )
        session.add(trig)
        a.trigger_count = int(a.trigger_count or 0) + 1
        a.last_triggered_at = datetime.utcnow()
        session.add(a); session.commit(); session.refresh(trig)
        return CommentSimulateResponse(
            matched=True, matched_keyword=mk,
            rendered_reply=rendered_reply, rendered_dm=rendered_dm,
            trigger_id=trig.id,
            reply_sent=bool(rendered_reply), dm_sent=bool(rendered_dm),
        )
    return CommentSimulateResponse(matched=False)


@app.get("/comment-automations.html", response_class=FileResponse)
def comment_automations_page():
    return FileResponse("frontend/comment-automations.html")


# =========================================================================
# Phase 2 #5 — Carousel / multi-slide editor
# =========================================================================

def _assert_post_owner(post_id: int, user_id: int, session: Session) -> Post:
    p = session.get(Post, post_id)
    if not p:
        raise HTTPException(status_code=404, detail="Post not found")
    c = session.get(Campaign, p.campaign_id) if p.campaign_id else None
    if not c or c.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not allowed")
    return p


@app.get("/posts/{post_id}", response_model=PostOut)
def get_post_one(
    post_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PostOut:
    p = _assert_post_owner(post_id, current_user.id, session)
    return _post_to_out(p)


@app.get("/post-editor.html", response_class=FileResponse)
def post_editor_page():
    return FileResponse("frontend/post-editor.html")


@app.get("/posts/{post_id}/slides")
def get_post_slides(
    post_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    p = _assert_post_owner(post_id, current_user.id, session)
    return {
        "slides": _slides_list(p.slides),
        "is_carousel": bool(getattr(p, "is_carousel", False)),
    }


@app.put("/posts/{post_id}/slides", response_model=PostOut)
def update_post_slides(
    post_id: int,
    body: UpdateSlidesRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PostOut:
    p = _assert_post_owner(post_id, current_user.id, session)
    # normalize + re-order
    normalized = []
    for i, s in enumerate(body.slides):
        normalized.append({
            "image_url": (s.image_url or "").strip(),
            "caption_overlay": (s.caption_overlay or "").strip(),
            "alt_text": (s.alt_text or "").strip(),
            "order": i,
        })
    p.slides = json.dumps(normalized)
    if body.is_carousel is not None:
        p.is_carousel = bool(body.is_carousel)
    else:
        p.is_carousel = len(normalized) >= 2
    # If first slide has image_url and the post's main image is empty, sync it
    if normalized and not (p.image_url or "").strip():
        p.image_url = normalized[0]["image_url"]
    session.add(p); session.commit(); session.refresh(p)
    return _post_to_out(p)


@app.post("/posts/{post_id}/slides/generate", response_model=GenerateSlidesResponse)
async def generate_post_slides(
    post_id: int,
    body: GenerateSlidesRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> GenerateSlidesResponse:
    """Use the LLM to split a caption into carousel slide text."""
    p = _assert_post_owner(post_id, current_user.id, session)
    base_caption = (p.caption or "").strip() if body.use_existing_caption else ""
    theme = (body.theme or "").strip() or base_caption
    if not theme:
        raise HTTPException(status_code=400, detail="Provide a theme or use an existing caption")
    style = body.style or "educational"
    count = int(body.count)
    system = (
        "You are a social-media carousel writer. Output STRICT JSON with key "
        '"slides" as an array of objects {caption_overlay, alt_text}. '
        "caption_overlay is short text that fits on an image (1-2 sentences, "
        "<=120 chars). alt_text describes the visual in <=120 chars. "
        "Do not include the image URL."
    )
    user_msg = (
        f"Create {count} carousel slides in a '{style}' style for this topic:\n\n"
        f"{theme}\n\n"
        "Slide 1 must hook attention. Final slide must include a call-to-action."
    )
    try:
        result = await _openai_json(system, user_msg, temperature=0.7)
    except Exception as e:
        log.warning("slide gen failed: %s", e)
        result = {}
    arr = result.get("slides") if isinstance(result, dict) else None
    slides: list[CarouselSlide] = []
    if isinstance(arr, list):
        for i, item in enumerate(arr[:count]):
            if not isinstance(item, dict):
                continue
            slides.append(CarouselSlide(
                image_url="",
                caption_overlay=str(item.get("caption_overlay") or "")[:300],
                alt_text=str(item.get("alt_text") or "")[:200],
                order=i,
            ))
    # Fallback: split caption into chunks
    if not slides and base_caption:
        sentences = [s.strip() for s in base_caption.replace("!", ".").replace("?", ".").split(".") if s.strip()]
        for i, sent in enumerate(sentences[:count]):
            slides.append(CarouselSlide(image_url="", caption_overlay=sent[:120], alt_text=sent[:120], order=i))
    return GenerateSlidesResponse(slides=slides)


# =========================================================================
# Phase 2 #6 — Canva integration
# =========================================================================

def _canva_design_out(d: CanvaDesign) -> CanvaDesignOut:
    try:
        af = json.loads(d.autofill_data) if d.autofill_data else {}
    except Exception:
        af = {}
    return CanvaDesignOut(
        id=d.id, user_id=d.user_id, post_id=d.post_id, campaign_id=d.campaign_id,
        external_id=d.external_id or "", template_id=d.template_id or "",
        title=d.title or "", design_type=d.design_type or "instagram-post",
        edit_url=d.edit_url or "", share_url=d.share_url or "",
        thumbnail_url=d.thumbnail_url or "", export_url=d.export_url or "",
        export_format=d.export_format or "png",
        prompt=d.prompt or "",
        autofill_data=af if isinstance(af, dict) else {},
        status=d.status or "pending", error=d.error or "",
        created_at=d.created_at, updated_at=d.updated_at,
    )


_CANVA_DESIGN_TYPE_TO_URL = {
    "instagram-post": "https://www.canva.com/design/?category=tAFwXW2Fjj4",
    "instagram-story": "https://www.canva.com/design/?category=tAFwdjEIkDA",
    "instagram-reel": "https://www.canva.com/design/?category=tAFwhUSGqJo",
    "facebook-post": "https://www.canva.com/design/?category=tAFwhxwXibg",
    "facebook-cover": "https://www.canva.com/design/?category=tAFwoVfMvgs",
    "linkedin-post": "https://www.canva.com/design/?category=tACZClbBh4s",
    "linkedin-banner": "https://www.canva.com/design/?category=tAFwEjJrLg8",
    "presentation": "https://www.canva.com/design/?category=tACZCns7aVM",
    "square-post": "https://www.canva.com/design/?category=tACZCjWMEfY",
    "vertical-video": "https://www.canva.com/design/?category=tAFwJOBAv1w",
    "custom": "https://www.canva.com/",
}


@app.get("/canva/status", response_model=CanvaStatusResponse)
def canva_status(current_user: User = Depends(get_current_user)) -> CanvaStatusResponse:
    token = (os.getenv("CANVA_API_TOKEN") or "").strip()
    client_id = (os.getenv("CANVA_CLIENT_ID") or "").strip()
    # If API token present, we're in "api" mode; otherwise we use link-out
    mode = "api" if token else "link-out"
    return CanvaStatusResponse(
        configured=True,  # link-out is always available
        connected=bool(token),
        auth_url=f"https://www.canva.com/api/oauth/authorize?client_id={client_id}" if client_id else "",
        mode=mode,
    )


@app.post("/canva/designs", response_model=CanvaDesignOut)
async def create_canva_design(
    body: CanvaDesignCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CanvaDesignOut:
    """Create a design record.

    Modes:
    - **link-out** (default): returns a deep link to Canva's editor with title +
      prompt pre-encoded in the URL, so the user can build the design manually.
    - **API autofill** (when CANVA_API_TOKEN is set + template_id provided):
      calls Canva Connect `/v1/autofills` to programmatically fill a brand
      template with the caption / caption fields, then returns the resulting
      edit URL.
    """
    if body.post_id is not None:
        _assert_post_owner(body.post_id, current_user.id, session)

    from urllib.parse import urlencode

    canva_token = (os.getenv("CANVA_API_TOKEN") or "").strip()
    template_id = (body.template_id or "").strip()

    edit_url = ""
    external_id = ""
    autofill_status = "pending"
    autofill_error = ""
    resolved_autofill_data: dict = body.autofill_data or {}

    # ── API autofill path ─────────────────────────────────────────────────────
    if canva_token and template_id:
        try:
            import httpx

            # Build autofill data payload from body fields + caption
            # The caller may pass arbitrary key→value pairs in autofill_data;
            # we wrap each as a Canva "text" field.
            data_fields: dict = {}
            for field_key, field_value in (resolved_autofill_data or {}).items():
                if isinstance(field_value, str):
                    data_fields[field_key] = {"type": "text", "text": field_value[:5000]}

            # Add prompt/caption as a "headline" field if not already present
            if body.prompt and "headline" not in data_fields:
                data_fields["headline"] = {"type": "text", "text": body.prompt[:5000]}
            if body.title and "title" not in data_fields:
                data_fields["title"] = {"type": "text", "text": body.title[:500]}

            canva_payload = {
                "brand_template_id": template_id,
                "title": body.title or f"BrokerAI {body.design_type}",
                "data": data_fields,
            }

            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    "https://api.canva.com/rest/v1/autofills",
                    json=canva_payload,
                    headers={
                        "Authorization": f"Bearer {canva_token}",
                        "Content-Type": "application/json",
                    },
                )

            if resp.status_code in (200, 201):
                resp_data = resp.json()
                # Canva returns { "job": { "id": "...", "status": "..." } }
                job = resp_data.get("job") or {}
                external_id = job.get("id") or ""
                autofill_status = "autofill_submitted"
                # The edit URL is available after the job completes; poll or
                # store the job ID so the client can fetch it later.
                # For now we return the job ID as a placeholder edit_url so the
                # frontend can query /canva/designs/{id}/status.
                edit_url = job.get("urls", {}).get("edit_url") or ""
                log.info(
                    "[canva] autofill job submitted external_id=%s user_id=%s",
                    external_id, current_user.id,
                )
            else:
                log.warning(
                    "[canva] autofill API error status=%s body=%s",
                    resp.status_code, resp.text[:300],
                )
                autofill_error = f"Canva API {resp.status_code}: {resp.text[:200]}"
                autofill_status = "error"
                # Fall through to link-out below

        except Exception as exc:
            log.exception("[canva] autofill call failed: %s", exc)
            autofill_error = str(exc)[:300]
            autofill_status = "error"

    # ── Link-out path (fallback / no token) ───────────────────────────────────
    if not edit_url:
        base_url = _CANVA_DESIGN_TYPE_TO_URL.get(body.design_type, "https://www.canva.com/")
        query = urlencode({
            "title": body.title or f"BrokerAI {body.design_type}",
            "prompt": (body.prompt or "")[:500],
        })
        edit_url = f"{base_url}&{query}" if "?" in base_url else f"{base_url}?{query}"
        if autofill_status == "pending":
            autofill_status = "link-out"

    d = CanvaDesign(
        user_id=current_user.id,
        post_id=body.post_id,
        campaign_id=body.campaign_id,
        external_id=external_id,
        template_id=template_id,
        title=body.title or "",
        design_type=body.design_type,
        edit_url=edit_url,
        prompt=body.prompt or "",
        autofill_data=json.dumps(resolved_autofill_data),
        status=autofill_status,
        error=autofill_error,
    )
    session.add(d); session.commit(); session.refresh(d)
    return _canva_design_out(d)


@app.get("/canva/designs", response_model=CanvaDesignListResponse)
def list_canva_designs(
    post_id: Optional[int] = None,
    status: Optional[str] = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CanvaDesignListResponse:
    q = select(CanvaDesign).where(CanvaDesign.user_id == current_user.id)
    if post_id is not None:
        q = q.where(CanvaDesign.post_id == post_id)
    if status:
        q = q.where(CanvaDesign.status == status)
    rows = list(session.exec(q).all())
    rows.sort(key=lambda d: d.created_at, reverse=True)
    return CanvaDesignListResponse(
        items=[_canva_design_out(d) for d in rows],
        total=len(rows),
    )


@app.get("/canva/designs/{design_id}", response_model=CanvaDesignOut)
def get_canva_design(
    design_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CanvaDesignOut:
    d = session.get(CanvaDesign, design_id)
    if not d or d.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Design not found")
    return _canva_design_out(d)


@app.post("/canva/designs/{design_id}/import", response_model=CanvaDesignOut)
def import_canva_design(
    design_id: int,
    body: CanvaDesignImportRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> CanvaDesignOut:
    d = session.get(CanvaDesign, design_id)
    if not d or d.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Design not found")
    d.export_url = body.export_url.strip()
    d.thumbnail_url = (body.thumbnail_url or "").strip() or d.thumbnail_url
    d.share_url = (body.share_url or "").strip() or d.share_url
    d.export_format = body.export_format
    d.status = "imported"
    d.updated_at = datetime.utcnow()
    session.add(d)

    if body.attach_to_post and d.post_id:
        p = session.get(Post, d.post_id)
        if p:
            c = session.get(Campaign, p.campaign_id) if p.campaign_id else None
            if c and c.user_id == current_user.id:
                if body.as_slide:
                    current = _slides_list(p.slides)
                    current.append({
                        "image_url": d.export_url,
                        "caption_overlay": "",
                        "alt_text": d.title or "",
                        "order": len(current),
                    })
                    p.slides = json.dumps(current)
                    p.is_carousel = len(current) >= 2
                    if not (p.image_url or "").strip():
                        p.image_url = d.export_url
                else:
                    p.image_url = d.export_url
                session.add(p)
    session.commit(); session.refresh(d)
    return _canva_design_out(d)


@app.delete("/canva/designs/{design_id}")
def delete_canva_design(
    design_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    d = session.get(CanvaDesign, design_id)
    if not d or d.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Design not found")
    session.delete(d); session.commit()
    return {"ok": True, "deleted_id": design_id}


@app.get("/health")
@app.get("/healthz")
async def health():
    """Liveness + readiness probe. Kept small and fast for UptimeRobot.

    Pings the DB (SELECT 1); if that fails we return 503 so monitors alert.
    """
    db_ok = True
    db_error: Optional[str] = None
    try:
        with Session(engine) as s:
            s.exec(select(1)).one()
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        db_error = type(exc).__name__
        log_event("health.db_fail", level=logging.ERROR, error=db_error)

    payload: Dict[str, Any] = {
        "status": "ok" if db_ok else "degraded",
        "ok": db_ok,
        "version": APP_VERSION,
        "uptime_seconds": int(time.time() - APP_BOOT_TIME),
        "db": "ok" if db_ok else "fail",
        "openai_configured": bool(_openai_api_key()),
        "unsplash_configured": bool(os.getenv("UNSPLASH_ACCESS_KEY", "").strip()),
        "replicate_configured": bool(os.getenv("REPLICATE_API_TOKEN", "").strip()),
    }
    if db_error:
        payload["db_error"] = db_error
    if not db_ok:
        return JSONResponse(status_code=503, content=payload)
    return payload


if __name__ == "__main__":
    import uvicorn

    _port = int(os.getenv("PORT", "8000"))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=_port, reload=False)
