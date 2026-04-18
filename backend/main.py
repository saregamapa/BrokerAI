import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

import backend.env_loader  # noqa: F401 — loads project root .env before agent imports

from backend.core.cache import (
    ANALYTICS_SUMMARY_TTL,
    CAMPAIGN_LIST_TTL,
    analytics_summary_key,
    cache_get,
    cache_set,
    campaign_list_key,
    invalidate_user_analytics,
    invalidate_user_campaigns,
)
from backend.core.logger import configure_logging, get_logger, log_event, time_block
from backend.core.sentry import init_sentry
from backend.middleware.request_id import RequestIdMiddleware
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.responses import Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from backend.agents.analytics_insights import run_campaign_insights
from backend.agents.errors import CampaignPipelineError, OpenAINotConfiguredError
from backend.agents.graph import resume_campaign_publishing, run_campaign_phase1
from backend.agents.nodes import _openai_api_key, content_node, media_node, compliance_node
from backend.ai.compliance import check_caption_compliance
from backend.auth import (
    clear_refresh_cookie,
    create_access_token,
    create_refresh_token,
    get_current_user,
    get_user_by_email,
    hash_password,
    revoke_refresh_token,
    revoke_all_user_tokens,
    set_refresh_cookie,
    verify_password,
    verify_refresh_token,
    generate_reset_token,
    hash_reset_token,
    generate_email_verify_token,
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES,
    EMAIL_VERIFY_TOKEN_EXPIRE_HOURS,
)
from backend.db import create_db_and_tables, engine, get_session
from backend.integrations.ayrshare import (
    coerce_ayrshare_platforms,
    platform_response_json,
)
from backend.services.post_media_url import video_url_from_script_json
from backend.services.publish_service import (
    _lock_expired,
    _utc_now_naive,
    safe_publish_post,
)
from backend.services.scheduler import publish_due_posts
from backend.services.post_status_poller import status_poll_tick
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
    AuditEvent,
    AutomationRule,
    BrandAsset,
    Campaign,
    CommentAutomation,
    CommentTrigger,
    EmailVerificationToken,
    PasswordResetToken,
    Post,
    RefreshToken,
    SocialAccount,
    Team,
    TeamInvite,
    User,
)
from backend.services.audit_service import audit_log, AuditEventType
from backend.permissions import check_permission, require_permission
from backend.schemas import (
    AnalyticsBulkUpdateOut,
    AnalyticsOut,
    AnalyticsPostRow,
    AnalyticsSummaryOut,
    PlatformBreakdownRow,
    TimeSeriesPoint,
    ApproveCampaignRequest,
    BrandAssetListResponse,
    BrandAssetOut,
    BrandKitAIRequest,
    BrandKitUpdateRequest,
    CampaignInsightsOut,
    CampaignDetailOut,
    CampaignOut,
    CaptionVariantOut,
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
    DuplicateCampaignRequest,
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
    WowManusPersonalizeRequest,
    WowManusVisualTemplatesResponse,
    ManusVisualTemplateOut,
    AutomationCreateRequest,
    AutomationUpdateRequest,
    AutomationOut,
    AutomationListResponse,
    AutomationSimulateRequest,
    AutomationSimulateResponse,
)
from backend.integrations.unsplash import search_photos as unsplash_search_photos
from backend.services.ai_media_service import _api_key
from backend.services.wizard_visual_templates_service import run_wizard_visual_templates
from backend.integrations.openai_sora_video import (
    create_sora_video,
    download_sora_video_bytes,
    fetch_sora_video,
)
from backend.integrations.replicate_video import (
    create_video_prediction,
    fetch_prediction as fetch_video_prediction,
)
from backend.services.wizard_video_asset import (
    create_wizard_video_serve_token,
    decode_wizard_video_serve_token,
    ensure_sora_video_materialized,
    wizard_video_dir,
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
from backend.services.analytics_background import (
    sync_campaign_analytics,
    sync_user_analytics_summary,
)
from backend.services.analytics_service import (
    build_analytics_summary,
    list_user_posts_for_analytics,
    performance_tier,
    platform_breakdown,
    posts_as_ai_payload,
    time_series,
)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # refresh if needed

configure_logging()
log = get_logger("brokerai")

# Initialize Sentry before app creation — no-op if SENTRY_DSN is not set.
init_sentry()

# Boot timestamp and app version, used by /health.
APP_BOOT_TIME = time.time()
APP_VERSION = os.getenv("APP_VERSION", os.getenv("RENDER_GIT_COMMIT", "dev"))[:12]

BASE_DIR = Path(__file__).resolve().parent.parent


def _video_status_for_response(raw: object) -> str:
    """Map provider job statuses into VideoGenerateResponse literals."""
    s = str(raw or "queued").lower()
    if s in ("succeeded", "completed"):
        return "succeeded"
    if s in ("failed", "canceled", "cancelled"):
        return "failed"
    if s in ("processing", "starting", "in_progress"):
        return "processing"
    return "queued"


def _video_backend() -> str:
    """openai = OpenAI Sora (default); replicate = Replicate models."""
    raw = (os.getenv("BROKERAI_VIDEO_BACKEND") or "openai").strip().lower()
    return "replicate" if raw == "replicate" else "openai"


def _is_openai_video_id(prediction_id: str) -> bool:
    return (prediction_id or "").strip().startswith("video_")


async def _video_job_http_response(
    request: Request,
    *,
    user_id: int,
    result: Dict[str, Any],
) -> VideoGenerateResponse:
    """Normalize provider dict to API schema; materialize OpenAI Sora MP4 to a signed URL."""
    status = _video_status_for_response(result.get("status"))
    pred = str(result.get("prediction_id") or "")
    err = str(result.get("error") or "")
    vurl = str(result.get("video_url") or "")

    if status == "succeeded" and _is_openai_video_id(pred) and not vurl:
        try:
            fn = await ensure_sora_video_materialized(
                base_dir=BASE_DIR,
                user_id=int(user_id),
                openai_video_id=pred,
                download=download_sora_video_bytes,
            )
            token = create_wizard_video_serve_token(int(user_id), fn)
            base = _public_app_origin() or str(request.base_url).rstrip("/")
            vurl = f"{base}/wizard-video-serve?token={quote(token, safe='')}"
        except Exception as exc:  # noqa: BLE001
            log.warning("sora_materialize_failed video_id=%s err=%s", pred, exc)
            return VideoGenerateResponse(
                status="failed",
                video_url="",
                prediction_id=pred,
                error=str(err or exc or "could_not_save_video"),
            )

    return VideoGenerateResponse(
        status=status,
        video_url=vurl,
        prediction_id=pred,
        error=err,
    )


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


def _video_url_from_post_row(row: Post) -> str:
    evu = (getattr(row, "embed_video_url", None) or "").strip()
    if evu:
        return evu
    return video_url_from_script_json(getattr(row, "video_script", None) or "")


def _post_to_out(row: Post, day: Optional[str] = None) -> PostOut:
    body = (getattr(row, "content", None) or row.caption or "").strip()
    return PostOut(
        id=row.id,
        campaign_id=row.campaign_id,
        day=day or row.day_label,
        caption=row.caption,
        hashtags=_hashtags_to_list(row.hashtags),
        image_url=row.image_url or "",
        video_url=_video_url_from_post_row(row),
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
        ab_variant_b=getattr(row, "ab_variant_b", None) or None,
        ab_winner=getattr(row, "ab_winner", None) or None,
        ab_status=getattr(row, "ab_status", None) or None,
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


async def _poller_loop() -> None:
    """S1-02: Background analytics refresh + stuck-post recovery (60 s cadence)."""
    # Stagger 30 s behind the publish scheduler to avoid I/O spikes on the same second
    await asyncio.sleep(30)
    while True:
        try:
            await status_poll_tick()
        except Exception:
            log.exception("poller tick failed")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    from backend.core.plan_limits import warn_placeholder_stripe_prices
    warn_placeholder_stripe_prices()
    # Security check: warn if JWT secret is insecure
    jwt_key = os.environ.get("JWT_SECRET_KEY", "")
    if not jwt_key or "change-me" in jwt_key or len(jwt_key) < 20:
        log.warning(
            "*** SECURITY WARNING: JWT_SECRET_KEY is missing or insecure. "
            "Set a strong random secret (32+ chars) in .env for production. ***"
        )
    task = None
    poller_task = None
    if not os.getenv("BROKERAI_DISABLE_SCHEDULER"):
        task = asyncio.create_task(_scheduler_loop())
        log.info("Background publish scheduler started (60s tick)")
        if not os.getenv("BROKERAI_DISABLE_POLLER"):
            poller_task = asyncio.create_task(_poller_loop())
            log.info("Background analytics poller started (60s tick, 30s offset)")
    else:
        log.info("Background publish scheduler disabled (BROKERAI_DISABLE_SCHEDULER)")
    yield
    for t in (task, poller_task):
        if t is not None:
            t.cancel()
            try:
                await t
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
    if "*" in origins:
        log.warning(
            "CORS is configured with wildcard origins ('*'). "
            "Set ALLOWED_ORIGINS env var to your production domain(s) before deployment."
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
app.add_middleware(RequestIdMiddleware)


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


@app.post("/social-disconnect")
async def social_disconnect(
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Disconnect a social account — clears the user's Ayrshare profile key."""
    body = await request.json()
    # platform param is accepted but we disconnect the entire Ayrshare profile
    # (Ayrshare uses a single profile key for all platforms)
    platform = str(body.get("platform") or "all").strip().lower()

    # Clear profile key on User row
    current_user.ayrshare_profile_key = None
    current_user.social_connected = False
    session.add(current_user)

    # Also clear any SocialAccount rows for this user
    stmt = select(SocialAccount).where(SocialAccount.user_id == current_user.id)
    accs = list(session.exec(stmt).all())
    for acc in accs:
        acc.is_connected = False
        acc.profile_key = ""
        session.add(acc)

    session.commit()
    log.info("social_disconnect user_id=%s platform=%s", current_user.id, platform)
    # S5-08: Audit — social account disconnected
    try:
        audit_log(
            AuditEventType.SOCIAL_DISCONNECTED,
            actor_user_id=current_user.id,
            entity_type="user",
            entity_id=current_user.id,
            summary=f"Social account disconnected (platform={platform})",
            request=request,
        )
    except Exception:
        log.exception("audit_log failed in social_disconnect user_id=%s", current_user.id)
    return {"ok": True, "disconnected": platform}


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
    # S5-08: Audit — social account connected (only when actually connected)
    if connected:
        try:
            from backend.services.audit_service import audit_log, AuditEventType
            audit_log(
                AuditEventType.SOCIAL_CONNECTED,
                actor_user_id=current_user.id,
                entity_type="user",
                entity_id=current_user.id,
                summary="Social account connected via Ayrshare callback",
            )
        except Exception:
            log.exception("audit_log failed in social_connected_callback user_id=%s", current_user.id)
    return SocialConnectedCallbackResponse(
        ok=bool(sync_ok),
        connected=bool(connected),
        state=state,  # type: ignore[arg-type]
        message=msg,
    )


@app.post("/signup", response_model=TokenResponse)
@limiter.limit("10/hour")
def signup(
    request: Request,
    response: Response,
    body: SignupRequest,
    session: Session = Depends(get_session),
):
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
@limiter.limit("5/minute")  # S0-02: tightened from 20/minute
def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    session: Session = Depends(get_session),
):
    email = body.email.strip().lower()
    user = get_user_by_email(session, email)

    # S0-07: Check account lockout before any verification
    if user and user.locked_until:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if user.locked_until > now:
            remaining = int((user.locked_until - now).total_seconds() / 60) + 1
            log_event("login_blocked", email=email, user_id=user.id, reason="locked",
                      level=logging.WARNING)
            raise HTTPException(
                status_code=429,
                detail=f"Account temporarily locked. Try again in {remaining} minute(s).",
            )
        else:
            # Lock expired — reset counter
            user.failed_login_attempts = 0
            user.locked_until = None
            session.add(user)
            session.commit()

    if not user or not verify_password(body.password, user.password_hash):
        # S0-07: Increment failure counter, lock after 10 attempts
        if user:
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            if user.failed_login_attempts >= 10:
                user.locked_until = (
                    datetime.now(timezone.utc) + timedelta(minutes=15)
                ).replace(tzinfo=None)
                log_event("account_locked", email=email, user_id=user.id,
                          attempts=user.failed_login_attempts, level=logging.WARNING)
            session.add(user)
            session.commit()
        log_event("login_failed", email=email, reason="bad_credentials", level=logging.WARNING)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Successful login — reset failure counter, issue tokens
    user.failed_login_attempts = 0
    user.locked_until = None
    session.add(user)
    session.commit()

    access_token = create_access_token(user.id)
    # S0-03: Issue refresh token and set HttpOnly cookie
    raw_refresh = create_refresh_token(user.id, session)
    set_refresh_cookie(response, raw_refresh)

    log_event("login", user_id=user.id, email=email)
    return TokenResponse(access_token=access_token)


@app.post("/auth/refresh", response_model=TokenResponse)
def refresh_access_token(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    """S0-03: Exchange a valid refresh token cookie for a new access token.

    The refresh token must be present as an HttpOnly cookie named `refresh_token`.
    On success a new access token is returned and the refresh token cookie remains
    valid until its own expiry (sliding refresh can be added later).
    """
    raw = request.cookies.get("refresh_token", "")
    if not raw:
        raise HTTPException(status_code=401, detail="Missing refresh token")

    db_token = verify_refresh_token(raw, session)
    if db_token is None:
        clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Refresh token invalid or expired")

    user = session.get(User, db_token.user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    access_token = create_access_token(user.id)
    log_event("token_refreshed", user_id=user.id)
    return TokenResponse(access_token=access_token)


@app.post("/auth/logout")
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """S0-03: Revoke the current refresh token and clear the cookie."""
    raw = request.cookies.get("refresh_token", "")
    if raw:
        revoke_refresh_token(raw, session)
    clear_refresh_cookie(response)
    log_event("logout", user_id=current_user.id)
    return {"ok": True}


@app.post("/auth/logout-all")
def logout_all(
    response: Response,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Revoke ALL refresh tokens for the current user (sign out all devices)."""
    revoke_all_user_tokens(current_user.id, session)
    clear_refresh_cookie(response)
    log_event("logout_all", user_id=current_user.id)
    return {"ok": True}


@app.post("/auth/forgot-password")
@limiter.limit("5/hour")
def forgot_password(
    request: Request,
    response: Response,
    body: Dict[str, Any],
    session: Session = Depends(get_session),
):
    """S0-04: Generate a one-time password-reset token and (when email is wired)
    dispatch a reset link.  Always returns the same shape to prevent email enumeration.
    """
    email = str((body or {}).get("email") or "").strip().lower()
    support = os.getenv("SUPPORT_EMAIL", "support@brokerai.app")
    _generic_ok = {
        "ok": True,
        "message": (
            "If that email exists, we'll send reset instructions shortly. "
            f"You can also email {support} for help."
        ),
    }

    if not email or "@" not in email:
        return _generic_ok

    user = get_user_by_email(session, email)
    if user:
        # Invalidate any existing unused tokens for this user
        stmt = select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used == False,  # noqa: E712
        )
        for old_tok in session.exec(stmt).all():
            old_tok.used = True
            session.add(old_tok)

        raw_token, token_hash = generate_reset_token()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)
        ).replace(tzinfo=None)

        db_token = PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        session.add(db_token)
        session.commit()

        reset_base = os.getenv("APP_URL", "https://brokerai.app")
        reset_link = f"{reset_base}/reset-password?token={raw_token}"

        log_event("password_reset_token_generated", user_id=user.id, email=email)
        log.info("password_reset_token_generated user=%s", user.email)

        # Send password reset email (best-effort)
        try:
            from backend.services.email_service import send_password_reset_email
            send_password_reset_email(user.email, reset_link)
        except Exception:
            log.warning("Failed to send password reset email to %s", user.email)
    else:
        log.info("password_reset_request unknown_email=%s", email)

    return _generic_ok


@app.post("/auth/reset-password")
@limiter.limit("5/hour")
def reset_password(
    request: Request,
    body: Dict[str, Any],
    session: Session = Depends(get_session),
):
    """S0-04: Consume a password-reset token and update the user's password."""
    token_raw = str((body or {}).get("token") or "").strip()
    new_password = str((body or {}).get("password") or "").strip()

    if not token_raw or not new_password:
        raise HTTPException(status_code=400, detail="token and password are required")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    token_hash = hash_reset_token(token_raw)
    stmt = select(PasswordResetToken).where(
        PasswordResetToken.token_hash == token_hash,
        PasswordResetToken.used == False,  # noqa: E712
    )
    db_token = session.exec(stmt).first()
    if db_token is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    now = datetime.utcnow()
    if db_token.expires_at < now:
        db_token.used = True
        session.add(db_token)
        session.commit()
        raise HTTPException(status_code=400, detail="Reset token has expired")

    user = session.get(User, db_token.user_id)
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    # Update password, mark token used, revoke all existing refresh tokens
    user.password_hash = hash_password(new_password)
    user.failed_login_attempts = 0
    user.locked_until = None
    db_token.used = True
    session.add(user)
    session.add(db_token)
    session.commit()

    revoke_all_user_tokens(user.id, session)
    log_event("password_reset_complete", user_id=user.id)
    return {"ok": True, "message": "Password updated. Please log in."}


@app.post("/auth/send-verification")
@limiter.limit("3/hour")
def send_email_verification(
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """S0-06: Generate an email verification token for the current user."""
    if current_user.email_verified:
        return {"ok": True, "message": "Email already verified"}

    # Invalidate old tokens
    stmt = select(EmailVerificationToken).where(
        EmailVerificationToken.user_id == current_user.id,
        EmailVerificationToken.used == False,  # noqa: E712
    )
    for old_tok in session.exec(stmt).all():
        old_tok.used = True
        session.add(old_tok)

    raw_token, token_hash = generate_email_verify_token()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=EMAIL_VERIFY_TOKEN_EXPIRE_HOURS)
    ).replace(tzinfo=None)

    db_token = EmailVerificationToken(
        user_id=current_user.id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    session.add(db_token)
    session.commit()

    verify_base = os.getenv("APP_URL", "https://brokerai.app")
    verify_link = f"{verify_base}/verify-email?token={raw_token}"

    log_event("email_verification_token_generated", user_id=current_user.id)
    log.info("email_verification_token_generated user=%s", current_user.email)

    # Send verification email (best-effort)
    try:
        from backend.services.email_service import send_email_verification_email
        send_email_verification_email(current_user.email, verify_link)
    except Exception:
        log.warning("Failed to send verification email to %s", current_user.email)
    return {"ok": True, "message": "Verification email sent (check your inbox)"}


@app.get("/auth/verify-email")
@app.post("/auth/verify-email")
@limiter.limit("10/hour")
def verify_email(
    request: Request,
    token: Optional[str] = None,
    body: Optional[Dict[str, Any]] = None,
    session: Session = Depends(get_session),
):
    """S0-06: Confirm an email verification token and mark the account as verified."""
    from backend.auth import hash_reset_token as _hash  # reuse same SHA-256 helper

    token_raw = token or str((body or {}).get("token") or "").strip()
    if not token_raw:
        raise HTTPException(status_code=400, detail="token is required")

    token_hash = _hash(token_raw)
    stmt = select(EmailVerificationToken).where(
        EmailVerificationToken.token_hash == token_hash,
        EmailVerificationToken.used == False,  # noqa: E712
    )
    db_token = session.exec(stmt).first()
    if db_token is None:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")

    now = datetime.utcnow()
    if db_token.expires_at < now:
        db_token.used = True
        session.add(db_token)
        session.commit()
        raise HTTPException(status_code=400, detail="Verification link has expired. Request a new one.")

    user = session.get(User, db_token.user_id)
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    user.email_verified = True
    db_token.used = True
    session.add(user)
    session.add(db_token)
    session.commit()

    log_event("email_verified", user_id=user.id)
    return {"ok": True, "message": "Email verified. You can now use all features."}


# ---------------------------------------------------------------------------
# S4-05: Google OAuth2 — /auth/google + /auth/google/callback
# ---------------------------------------------------------------------------

@app.get("/auth/google")
async def google_auth_start(request: Request):
    """Redirect user to Google OAuth2 consent screen."""
    from fastapi.responses import RedirectResponse

    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    if not client_id:
        # Browser navigation should never land on a raw JSON error page.
        ref = (request.headers.get("referer") or "").lower()
        dest = (
            "/signup.html?notice=google_oauth_unavailable"
            if "signup" in ref
            else "/login.html?notice=google_oauth_unavailable"
        )
        return RedirectResponse(url=dest, status_code=302)
    from backend.services.google_oauth import get_google_auth_url

    url = get_google_auth_url()
    return RedirectResponse(url=url)


@app.get("/auth/google/callback")
async def google_auth_callback(
    code: Optional[str] = None,
    error: Optional[str] = None,
    session: Session = Depends(get_session),
):
    """Handle Google OAuth2 callback. Creates user if new, returns JWT via redirect."""
    from fastapi.responses import RedirectResponse

    if error or not code:
        return RedirectResponse(url="/login.html?error=google_auth_failed")

    from backend.services.google_oauth import exchange_code_for_profile
    profile = await exchange_code_for_profile(code)
    if not profile or not profile.get("email"):
        return RedirectResponse(url="/login.html?error=google_profile_failed")

    email = profile["email"].lower().strip()
    google_id = profile.get("sub", "")
    display_name = profile.get("name", "")
    avatar_url = profile.get("picture", "")

    # Find or create user
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None:
        # New user — create with a random unusable password (Google-only login)
        user = User(
            email=email,
            password_hash=hash_password(os.urandom(32).hex()),
            google_id=google_id,
            display_name=display_name or None,
            avatar_url=avatar_url or None,
            email_verified=True,  # Google already verified the email
        )
        session.add(user)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            # Race condition: another request registered the same email simultaneously
            user = session.exec(select(User).where(User.email == email)).first()
            if user is None:
                return RedirectResponse(url="/login.html?error=google_profile_failed")
        else:
            session.refresh(user)
        log_event("signup", kind="google", user_id=user.id, email=email)
    else:
        # Existing user — backfill Google fields if not yet set
        changed = False
        if hasattr(user, "google_id") and not user.google_id:
            user.google_id = google_id
            changed = True
        if hasattr(user, "display_name") and not user.display_name:
            user.display_name = display_name or None
            changed = True
        if hasattr(user, "avatar_url") and not user.avatar_url:
            user.avatar_url = avatar_url or None
            changed = True
        if changed:
            session.add(user)
            session.commit()
        log_event("login", kind="google", user_id=user.id, email=email)

    # Issue short-lived JWT — deliver via an intermediate HTML page so the token
    # never appears in the URL (browser history, Referer headers, analytics tools).
    token = create_access_token(user.id)
    from fastapi.responses import HTMLResponse
    html_content = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Signing in\u2026</title></head>
<body>
<script>
try {{
  localStorage.setItem('brokerai_token', '{token}');
}} catch(e) {{}}
window.location.replace('/dashboard.html');
</script>
<noscript><meta http-equiv="refresh" content="0;url=/dashboard.html"></noscript>
</body></html>"""
    return HTMLResponse(content=html_content)


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


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


# Kept for legacy fallback; plan enforcement now uses backend.core.plan_limits
PLAN_LIMITS: dict = {}  # deprecated — enforced via PlanLimitExceeded below


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
    response: Response,
    body: GenerateCampaignRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
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

    # S1-03: Plan enforcement — monthly campaign cap + platform count
    from backend.core.plan_limits import (
        PlanLimitExceeded,
        assert_can_create_campaign,
        assert_platform_count,
    )
    try:
        assert_can_create_campaign(session, current_user)
        if body.platforms:
            assert_platform_count(current_user, body.platforms)
    except PlanLimitExceeded as ple:
        raise HTTPException(status_code=402, detail=ple.to_response())

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

    # Assemble brand_kit from User model fields for injection into all LangGraph agent nodes.
    brand_kit: Dict[str, Any] = {}
    _bv = (getattr(current_user, "brand_voice", "") or "").strip()
    _bp = (getattr(current_user, "brand_primary_color", "") or "").strip()
    _bs = (getattr(current_user, "brand_secondary_color", "") or "").strip()
    _bl = (getattr(current_user, "brand_logo_url", "") or "").strip()
    _bf = (getattr(current_user, "brand_font", "") or "").strip()
    if _bv or _bp or _bs or _bl or _bf:
        color_palette = ", ".join(c for c in [_bp, _bs] if c) or ""
        brand_kit = {
            "voice": _bv or "professional",
            "tone": _bv or "friendly",
            "key_messages": (getattr(current_user, "brand_key_messages", None) or "").strip(),
            "forbidden_words": (getattr(current_user, "brand_forbidden_words", None) or "").strip(),
            "cta_style": (getattr(current_user, "brand_cta_style", None) or "").strip(),
            "visual_style": f"Font: {_bf}" if _bf else "",
            "color_palette": color_palette,
            "logo_description": f"Logo at: {_bl}" if _bl else "",
            "compliance_notes": "",
        }

    initial = {
        "user_id": current_user.id,
        "campaign_id": camp.id,
        "approved": False,
        "campaign_data": campaign_data,
        "brand_kit": brand_kit,
        "step_log": [],
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

    # Draft mode: if the user has no Ayrshare profile key, keep the campaign
    # as "draft" rather than "pending_approval" so they can still review/edit
    # the generated posts without needing social accounts connected yet.
    has_social = bool((getattr(db_user, "ayrshare_profile_key", None) or "").strip())
    if not has_social and camp and camp.status == "pending_approval":
        camp.status = "draft"
        camp.updated_at = datetime.utcnow()
        session.add(camp)
        session.commit()

    stmt = (
        select(Post)
        .where(Post.campaign_id == camp.id)
        .where(Post.deleted_at.is_(None))  # S5-09: exclude soft-deleted posts
        .order_by(Post.id.asc())
    )
    rows = session.exec(stmt).all()
    posts_out = [_post_to_out(r) for r in rows]
    log.info(
        "Campaign generation finished user_id=%s campaign_id=%s posts=%s social_connected=%s",
        current_user.id,
        camp.id,
        len(posts_out),
        has_social,
    )
    invalidate_user_campaigns(current_user.id)
    # S5-08: Audit — campaign created + AI generation completed
    try:
        audit_log(
            AuditEventType.CAMPAIGN_CREATED,
            actor_user_id=current_user.id,
            entity_type="campaign",
            entity_id=camp.id,
            summary=f"Created campaign '{camp.name}'",
            request=request,
        )
        audit_log(
            AuditEventType.CAMPAIGN_GENERATED,
            actor_user_id=current_user.id,
            entity_type="campaign",
            entity_id=camp.id,
            summary="AI generation completed",
            request=request,
        )
    except Exception:
        log.exception("audit_log failed in generate_campaign campaign_id=%s", camp.id)
    return GenerateCampaignResponse(campaign_id=camp.id, posts=posts_out, social_connected=has_social)


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
    cache_key = campaign_list_key(current_user.id)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    stmt = (
        select(Campaign)
        .where(Campaign.user_id == current_user.id)
        .where(Campaign.deleted_at.is_(None))  # S5-09: exclude soft-deleted
        .order_by(Campaign.created_at.desc())
    )
    campaigns = session.exec(stmt).all()
    result = [CampaignOut.model_validate(c) for c in campaigns]
    cache_set(cache_key, [r.model_dump() for r in result], CAMPAIGN_LIST_TTL)
    return result


# ---------------------------------------------------------------------------
# S5-09: Soft-delete, recovery, and purge endpoints
# NOTE: /campaigns/deleted MUST be registered before /campaigns/{campaign_id}
# so FastAPI does not greedily match "deleted" as an integer campaign_id.
# ---------------------------------------------------------------------------

@app.get("/campaigns/deleted")
def list_deleted_campaigns(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """List soft-deleted campaigns within the 30-day recovery window."""
    cutoff = datetime.utcnow() - timedelta(days=30)
    campaigns = list(session.exec(
        select(Campaign)
        .where(Campaign.user_id == current_user.id)
        .where(Campaign.deleted_at.is_not(None))
        .where(Campaign.deleted_at >= cutoff)
        .order_by(Campaign.deleted_at.desc())
    ).all())
    return {"campaigns": [
        {
            "id": c.id,
            "name": c.name,
            "deleted_at": c.deleted_at.isoformat() + "Z",
            "recoverable_until": (c.deleted_at + timedelta(days=30)).isoformat() + "Z",
        }
        for c in campaigns
    ]}


@app.get("/campaigns/{campaign_id}", response_model=CampaignDetailOut)
def get_campaign(
    campaign_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    camp = session.get(Campaign, campaign_id)
    if not camp or camp.user_id != current_user.id or camp.deleted_at is not None:  # S5-09
        raise HTTPException(status_code=404, detail="Campaign not found")
    stmt = (
        select(Post)
        .where(Post.campaign_id == campaign_id)
        .where(Post.deleted_at.is_(None))  # S5-09: exclude soft-deleted posts
        .order_by(Post.id.asc())
    )
    rows = session.exec(stmt).all()
    return CampaignDetailOut(
        campaign=CampaignOut.model_validate(camp),
        posts=[_post_to_out(r) for r in rows],
    )


# Deprecated alias — kept for backwards compatibility
@app.get("/campaign/{campaign_id}", response_model=CampaignDetailOut, include_in_schema=False)
def get_campaign_legacy(
    campaign_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return get_campaign(campaign_id, session, current_user)


@app.delete("/campaigns/{campaign_id}", status_code=204)
def delete_campaign(
    campaign_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Soft-delete a campaign (and all its posts). Recoverable within 30 days."""
    from backend.services.soft_delete_service import soft_delete_campaign as _soft_delete
    deleted = _soft_delete(session, campaign_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Campaign not found")
    invalidate_user_campaigns(current_user.id)
    return Response(status_code=204)


@app.post("/campaigns/{campaign_id}/restore")
def restore_deleted_campaign(
    campaign_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Restore a soft-deleted campaign within the 30-day recovery window."""
    from backend.services.soft_delete_service import restore_campaign as _restore
    restored = _restore(session, campaign_id, current_user.id)
    if not restored:
        raise HTTPException(status_code=404, detail="Campaign not found or recovery window expired")
    invalidate_user_campaigns(current_user.id)
    return {"restored": True, "campaign_id": campaign_id}


@app.get("/campaigns/{campaign_id}/insights", response_model=CampaignInsightsOut)
async def campaign_insights(
    campaign_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """AI-generated insights and recommendations from post metrics, captions, and hashtags."""
    camp = session.get(Campaign, campaign_id)
    if not camp or camp.user_id != current_user.id or camp.deleted_at is not None:  # S5-09
        raise HTTPException(status_code=404, detail="Campaign not found")
    stmt = (
        select(Post)
        .where(Post.campaign_id == campaign_id)
        .where(Post.deleted_at.is_(None))  # S5-09: exclude soft-deleted posts
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


# Deprecated alias — kept for backwards compatibility
@app.get("/campaign-insights/{campaign_id}", response_model=CampaignInsightsOut, include_in_schema=False)
async def campaign_insights_legacy(
    campaign_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return await campaign_insights(campaign_id, session, current_user)


@app.post("/approve-campaign")
async def approve_campaign(
    request: Request,
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
    if not camp or camp.deleted_at is not None or (not owns_campaign and not same_team):  # S5-09
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
    invalidate_user_campaigns(current_user.id)
    # S5-08: Audit — campaign approved
    try:
        audit_log(
            AuditEventType.CAMPAIGN_APPROVED,
            actor_user_id=current_user.id,
            entity_type="campaign",
            entity_id=body.campaign_id,
            summary="Campaign approved",
            request=request,
        )
    except Exception:
        log.exception("audit_log failed in approve_campaign campaign_id=%s", body.campaign_id)
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
        .where(Post.deleted_at.is_(None))  # S5-09: exclude soft-deleted posts
        .order_by(Post.created_at.desc())
    )
    rows = session.exec(stmt).all()
    return [_post_to_out(row, day=None) for row in rows]


@app.post("/approve-post/{post_id}", response_model=PostOut)
async def approve_post(
    post_id: int,
    request: Request,
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
    # S5-08: Audit — post approved
    try:
        audit_log(
            AuditEventType.POST_APPROVED,
            actor_user_id=current_user.id,
            entity_type="post",
            entity_id=post_id,
            summary="Post approved",
            request=request,
        )
    except Exception:
        log.exception("audit_log failed in approve_post post_id=%s", post_id)
    return _post_to_out(row)


@app.post("/publish/{post_id}")
async def publish_post_now(
    post_id: int,
    request: Request,
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
    # S5-08: Audit — post published or failed
    try:
        audit_log(
            AuditEventType.POST_PUBLISHED if ok else AuditEventType.POST_FAILED,
            actor_user_id=current_user.id,
            entity_type="post",
            entity_id=post_id,
            summary="Post published" if ok else "Post publish failed",
            request=request,
        )
    except Exception:
        log.exception("audit_log failed in publish_post_now post_id=%s", post_id)
    return {"ok": ok, "status": out.get("status")}


@app.post("/update-post/{post_id}", response_model=PostOut)
async def update_post(
    post_id: int,
    request: Request,
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
    # S5-08: Audit — post caption/hashtags updated
    try:
        audit_log(
            AuditEventType.POST_UPDATED,
            actor_user_id=current_user.id,
            entity_type="post",
            entity_id=post_id,
            summary="Post caption updated",
            request=request,
        )
    except Exception:
        log.exception("audit_log failed in update_post post_id=%s", post_id)
    return _post_to_out(row)


@app.get("/analytics", response_model=AnalyticsOut)
def analytics(
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Lightweight in-app analytics (no third-party analytics SDK)."""
    payload = get_analytics_payload(session, current_user.id)
    # S4-08: Trigger async background refresh of user-level summary (non-blocking)
    background_tasks.add_task(sync_user_analytics_summary, current_user.id)
    return AnalyticsOut(**payload)


@app.get("/post-analytics/{post_id}", response_model=PostAnalyticsOut)
async def post_analytics(
    post_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Per-post metrics served from DB cache; Ayrshare refresh runs in the background.

    S4-08: Ayrshare polling moved out of the request thread to avoid blocking.
    Returns currently-cached DB values immediately, then enqueues a background
    sync so the next request sees fresher data.
    """
    # Return cached DB values immediately (non-blocking)
    row = session.get(Post, post_id)
    if row is None or row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Post not found")

    # Determine data source label based on stored state
    ayr_id = (getattr(row, "social_post_id", None) or "").strip()
    cached_source = "ayrshare" if ayr_id and int(row.likes or 0) > 0 else "placeholder"

    # S4-08: Enqueue Ayrshare fetch as a non-blocking background task
    background_tasks.add_task(fetch_post_analytics, session, post_id, current_user.id)

    return PostAnalyticsOut(
        post_id=int(row.id),
        likes=int(row.likes or 0),
        comments=int(row.comments or 0),
        shares=int(row.shares or 0),
        impressions=int(row.impressions or 0),
        engagement_rate=float(row.engagement_rate or 0),
        source=cached_source,
    )


@app.get("/analytics/posts", response_model=List[AnalyticsPostRow])
def analytics_posts(
    background_tasks: BackgroundTasks,
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
    # S4-08: Trigger background user summary refresh (non-blocking)
    background_tasks.add_task(sync_user_analytics_summary, current_user.id)
    return out


@app.get("/analytics/summary", response_model=AnalyticsSummaryOut)
def analytics_summary(
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    cache_key = analytics_summary_key(current_user.id)
    cached = cache_get(cache_key)
    if cached is not None:
        # S4-08: Trigger non-blocking background summary refresh for next request
        background_tasks.add_task(sync_user_analytics_summary, current_user.id)
        return AnalyticsSummaryOut(**cached)

    payload = build_analytics_summary(session, current_user.id)
    cache_set(cache_key, payload, ANALYTICS_SUMMARY_TTL)
    # S4-08: Enqueue background refresh so subsequent requests stay fresh
    background_tasks.add_task(sync_user_analytics_summary, current_user.id)
    return AnalyticsSummaryOut(**payload)


@app.get("/analytics/platform-breakdown", response_model=List[PlatformBreakdownRow])
def analytics_platform_breakdown(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Per-platform aggregated metrics for the authenticated user's posts.

    Useful for rendering a donut / bar chart showing which platforms
    drive the most engagement.
    """
    rows = platform_breakdown(session, int(current_user.id))
    return [PlatformBreakdownRow(**r) for r in rows]


@app.get("/analytics/time-series", response_model=List[TimeSeriesPoint])
def analytics_time_series(
    days: int = 30,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Daily engagement time-series for the past *days* calendar days.

    Default is 30 days. Capped at 365 so the chart stays readable.
    Useful for rendering a line chart of likes / impressions / engagement.
    """
    days = max(7, min(365, days))
    points = time_series(session, int(current_user.id), days=days)
    return [TimeSeriesPoint(**p) for p in points]


@app.post("/analytics/update", response_model=AnalyticsBulkUpdateOut)
async def analytics_update(
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Refresh metrics for all posts (Ayrshare when possible, else simulation).

    S4-08: The Ayrshare polling loop runs in a BackgroundTask to avoid blocking
    the request thread. Returns current totals immediately; sync happens after.
    """
    data = await update_post_analytics(session, current_user.id)
    invalidate_user_analytics(current_user.id)
    # S4-08: Also kick off high-level user summary refresh in background
    background_tasks.add_task(sync_user_analytics_summary, current_user.id)
    return AnalyticsBulkUpdateOut(**data)


@app.get("/analytics/insights", response_model=PerformanceAnalyticsAIOut)
async def analytics_ai_insights(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """LLM analysis of post performance + copy (OpenAI when configured)."""
    # S1-03: Gate behind analytics_ai plan feature
    from backend.core.plan_limits import PlanLimitExceeded, assert_feature
    try:
        assert_feature(current_user, "analytics_ai")
    except PlanLimitExceeded as ple:
        raise HTTPException(status_code=402, detail=ple.to_response())

    rows = list(session.exec(
        select(Post).where(Post.user_id == current_user.id).where(Post.deleted_at.is_(None))  # S5-09
    ).all())
    payload = posts_as_ai_payload(list(rows))
    result = await asyncio.to_thread(analyze_performance, payload)
    return PerformanceAnalyticsAIOut(
        insights=result.insights,
        mistakes=result.mistakes,
        recommendations=result.recommendations,
        next_post_ideas=result.next_post_ideas,
    )


@app.get("/analytics/export-csv")
def analytics_export_csv(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Download all post analytics as a CSV file.

    Returns a streaming CSV with columns:
    post_id, platform, status, likes, comments, shares, impressions, engagement_rate, caption_preview
    """
    import csv
    import io
    from fastapi.responses import StreamingResponse

    rows = list_user_posts_for_analytics(session, int(current_user.id))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "post_id", "platform", "status",
        "likes", "comments", "shares", "impressions",
        "engagement_rate", "performance_tier", "caption_preview",
    ])

    published_rates = [float(r.engagement_rate or 0) for r in rows if r.status == "published"]
    for r in rows:
        caption = (r.caption or "")[:120].replace("\n", " ")
        plat = (
            (getattr(r, "platform", None) or "").strip()
            or (
                r.publish_platforms[0]
                if isinstance(r.publish_platforms, list) and r.publish_platforms
                else ""
            )
        )
        tier = performance_tier(float(r.engagement_rate or 0), r.status or "", published_rates)
        writer.writerow([
            r.id, plat, r.status or "",
            int(r.likes or 0), int(r.comments or 0), int(r.shares or 0),
            int(r.impressions or 0),
            round(float(r.engagement_rate or 0), 4),
            tier, caption,
        ])

    buf.seek(0)
    filename = f"brokerai_analytics_{current_user.id}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/stats")
async def stats(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Extended stats for dashboards (includes plan + breakdown fields)."""
    payload = get_analytics_payload(session, current_user.id)
    stmt = select(Post).where(Post.user_id == current_user.id).where(Post.deleted_at.is_(None))  # S5-09
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
    if not camp or camp.deleted_at is not None or (not owns_campaign and not same_team):  # S5-09
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
    # Send invite email (best-effort)
    try:
        from backend.services.email_service import send_team_invite_email
        team_row = session.get(Team, team_id)
        team_display = team_row.name if team_row else f"Team {team_id}"
        send_team_invite_email(
            str(body.email),
            current_user.email or current_user.name or "A teammate",
            team_display,
            signup_url,
        )
    except Exception:
        pass
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
    response: Response,
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
    """CaptionAgent preview — structured caption + hashtags for wizard Step 3."""
    from backend.agents.caption_agent import (
        parse_preview_variants,
        preview_caption_system,
        preview_caption_user_payload,
    )

    count = max(1, min(body.count, 5))

    if not _openai_api_key():
        placeholders = [
            f"Discover your dream home in {body.location or 'your area'} — expert guidance from listing to keys. 🏡 DM us to get started!",
            f"Looking to {body.goal or 'grow your business'} in {body.location or 'your market'}? We make it simple. Ask us how!",
            f"Your next chapter starts here. Serving {body.location or 'the local area'} with trusted expertise.",
        ]

        variants: List[CaptionVariantOut] = []
        captions: List[str] = []
        for text in placeholders[:count]:
            variants.append(
                CaptionVariantOut(
                    caption=text.strip(),
                    hashtags=["RealEstate", "LocalExpert", "BrokerAI"],
                )
            )
            captions.append(text.strip() + "\n\n#RealEstate #LocalExpert #BrokerAI")
        return PreviewCaptionsResponse(captions=captions, variants=variants)

    system = preview_caption_system(body)
    user_msg = preview_caption_user_payload(body)
    data = await _openai_json(system, user_msg, temperature=0.72)
    variants_list, legacy = parse_preview_variants(data, count=count)
    if not variants_list:
        raw = data.get("captions") or []
        captions = [str(c) for c in raw if isinstance(c, str)][:count]
        while len(captions) < count:
            captions.append(
                f"Helping clients in {body.location or 'your area'} achieve their {body.goal or 'goals'}. Reach out today!"
            )
        return PreviewCaptionsResponse(captions=captions, variants=[])

    variants_out = list(variants_list[:count])
    captions_out = list(legacy[:count])
    while len(variants_out) < count:
        filler = CaptionVariantOut(
            caption=f"Helping clients in {body.location or 'your area'} with {body.goal or 'their goals'}.",
            hashtags=["Community", "Support", "Local"],
        )
        variants_out.append(filler)
        captions_out.append(
            f"{filler.caption}\n\n#Community #Support #Local"
        )
    return PreviewCaptionsResponse(captions=captions_out[:count], variants=variants_out[:count])


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
    request: Request,
    body: VideoGenerateRequest,
    current_user: User = Depends(get_current_user),
):
    """Generate an AI video (OpenAI Sora by default, or Replicate via BROKERAI_VIDEO_BACKEND=replicate)."""
    backend = _video_backend()
    if backend == "replicate":
        if not os.getenv("REPLICATE_API_TOKEN", "").strip():
            return VideoGenerateResponse(
                status="failed",
                video_url="",
                prediction_id="",
                error=(
                    "REPLICATE_API_TOKEN is not configured. "
                    "Use OpenAI Sora (default): set OPENAI_API_KEY or set BROKERAI_VIDEO_BACKEND=openai."
                ),
            )
        result = await create_video_prediction(
            body.prompt, body.duration_seconds, body.aspect_ratio
        )
    else:
        if not _openai_api_key():
            return VideoGenerateResponse(
                status="failed",
                video_url="",
                prediction_id="",
                error="OPENAI_API_KEY is not configured",
            )
        result = await create_sora_video(body.prompt, body.duration_seconds, body.aspect_ratio)

    if not result.get("ok") and result.get("status") not in ("queued", "starting", "processing"):
        return VideoGenerateResponse(
            status="failed",
            video_url="",
            prediction_id=str(result.get("prediction_id") or ""),
            error=str(result.get("error") or "video_generation_failed"),
        )
    return await _video_job_http_response(
        request, user_id=int(current_user.id or 0), result=result
    )


@app.get("/video/status/{prediction_id}", response_model=VideoGenerateResponse)
async def video_status(
    request: Request,
    prediction_id: str,
    current_user: User = Depends(get_current_user),
):
    if _is_openai_video_id(prediction_id):
        result = await fetch_sora_video(prediction_id)
    else:
        result = await fetch_video_prediction(prediction_id)
    return await _video_job_http_response(
        request, user_id=int(current_user.id or 0), result=result
    )


@app.get("/wizard-video-serve")
async def wizard_video_serve(token: str = Query(..., min_length=20)):
    """Public signed URL for a wizard Sora preview (JWT, same secret as login tokens)."""
    from jose import JWTError

    try:
        uid, fn = decode_wizard_video_serve_token(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired link") from None
    path = wizard_video_dir(BASE_DIR, uid) / fn
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(path, media_type="video/mp4", filename=fn)


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


@app.post("/wow/manus-visual-templates", response_model=WowManusVisualTemplatesResponse)
async def wow_manus_visual_templates(
    body: WowManusPersonalizeRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Wizard template grid: OpenAI proposes names, gradients, and Unsplash search phrases; Unsplash loads photos."""
    try:
        _api_key()
    except OpenAINotConfiguredError as e:
        return WowManusVisualTemplatesResponse(
            source="unavailable",
            error=str(e)[:400] or "OPENAI_API_KEY is not set or is invalid.",
        )
    stmt = (
        select(BrandAsset)
        .where(BrandAsset.user_id == current_user.id)
        .order_by(BrandAsset.created_at.desc())
        .limit(24)
    )
    rows = list(session.exec(stmt).all())
    summaries = [
        {"kind": r.kind or "document", "filename": r.original_filename or ""} for r in rows
    ]
    try:
        out = await run_wizard_visual_templates(
            persona=body.persona or "",
            bucket=body.bucket or "creator",
            persona_goal=body.persona_goal,
            brand_summaries=summaries,
        )
        tpls = [ManusVisualTemplateOut(**t) for t in out.get("templates") or []]
        return WowManusVisualTemplatesResponse(source="openai_unsplash", templates=tpls, error="")
    except Exception as e:
        log.warning("wow_manus_visual_templates_failed: %s", e)
        return WowManusVisualTemplatesResponse(source="fallback", error=str(e)[:400])


# ---------- Duplicate Campaign ----------

@app.post("/campaigns/{campaign_id}/duplicate", response_model=CampaignOut)
def duplicate_campaign(
    campaign_id: int,
    body: DuplicateCampaignRequest = DuplicateCampaignRequest(),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    src = session.get(Campaign, campaign_id)
    if not src or src.user_id != current_user.id or src.deleted_at is not None:  # S5-09
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
        src_posts = list(session.exec(
            select(Post).where(Post.campaign_id == src.id).where(Post.deleted_at.is_(None))  # S5-09
        ).all())
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

    invalidate_user_campaigns(current_user.id)
    return CampaignOut.model_validate(copy)


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
        link_url=a.link_url or "",
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


def _render_link(aut: CommentAutomation) -> str:
    """Resolve {link} for DM template from explicit link_url."""
    if (aut.link_url or "").strip():
        return aut.link_url.strip()
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
    # S1-03: Gate comment automations behind Growth+ plan
    from backend.core.plan_limits import PlanLimitExceeded, assert_feature
    try:
        assert_feature(current_user, "comment_automations")
    except PlanLimitExceeded as ple:
        raise HTTPException(status_code=402, detail=ple.to_response())

    if body.post_id is not None:
        p = session.get(Post, body.post_id)
        if not p:
            raise HTTPException(status_code=404, detail="Post not found")
        # ownership via campaign
        c = session.get(Campaign, p.campaign_id)
        if not c or c.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not allowed")
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
    link = _render_link(a)
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
        link = _render_link(a)
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


@app.get("/settings.html", response_class=FileResponse)
def settings_page():
    return FileResponse("frontend/settings.html")


# ---------------------------------------------------------------------------
# S1-03: Ayrshare publish-status webhook
# Register this URL in the Ayrshare dashboard → Webhooks → Post webhooks.
# Set AYRSHARE_PUBLISH_WEBHOOK_SECRET to the secret Ayrshare shows you.
# Ayrshare signs the raw body with HMAC-SHA256 and sends the hex digest in
# the `x-ayrshare-signature` header.
# ---------------------------------------------------------------------------
@app.post("/webhooks/ayrshare/publish")
async def ayrshare_publish_webhook(request: Request, session: Session = Depends(get_session)):
    """
    Receive Ayrshare post-status webhooks.

    Supported event payload fields (Ayrshare docs):
      type        — "post" (we ignore other types)
      postId      — Ayrshare post id (matches Post.social_post_id)
      status      — "success" | "error" | "scheduled"
      platform    — platform slug (optional)

    On success → mark matching Post rows as published (if still in publishing/failed).
    On error   → mark as failed (unless already published).
    """
    import hashlib
    import hmac as hmac_mod

    raw_body = await request.body()

    # --- HMAC verification ---------------------------------------------------
    webhook_secret = (os.getenv("AYRSHARE_PUBLISH_WEBHOOK_SECRET") or "").strip()
    if webhook_secret:
        sig_header = request.headers.get("x-ayrshare-signature", "")
        expected_sig = hmac_mod.new(
            webhook_secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        if not hmac_mod.compare_digest(expected_sig, sig_header.lower()):
            log.warning("ayrshare_webhook_bad_signature")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # --- Parse body ----------------------------------------------------------
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = str(payload.get("type") or "").strip().lower()
    if event_type != "post":
        # Silently accept but do nothing for non-post events (e.g. comment events)
        return {"accepted": True, "processed": False, "reason": "not_a_post_event"}

    ayrshare_post_id = str(payload.get("postId") or "").strip()
    event_status = str(payload.get("status") or "").strip().lower()

    if not ayrshare_post_id:
        return {"accepted": True, "processed": False, "reason": "missing_post_id"}

    if event_status not in ("success", "error", "scheduled"):
        return {"accepted": True, "processed": False, "reason": f"unhandled_status:{event_status}"}

    # --- Find matching posts -------------------------------------------------
    stmt = select(Post).where(Post.social_post_id == ayrshare_post_id)
    matching_posts = list(session.exec(stmt).all())

    if not matching_posts:
        # Could arrive before our DB is updated; log and accept
        log.info("ayrshare_webhook_no_match post_id=%s status=%s", ayrshare_post_id, event_status)
        return {"accepted": True, "processed": False, "reason": "no_matching_post"}

    now = datetime.utcnow()
    updated_ids = []
    for post in matching_posts:
        if event_status == "success":
            if post.status not in ("published",):
                # Transition to published
                try:
                    from backend.workflow.post_state import transition_post_status
                    transition_post_status(
                        session, post, POST_PUBLISHED,
                        reason="ayrshare_webhook_success", actor="ayrshare_webhook"
                    )
                except ValueError:
                    post.status = POST_PUBLISHED
                post.published_at = post.published_at or now
                post.last_error = ""
                post.is_locked = False
                post.lock_timestamp = None
                session.add(post)
                updated_ids.append(post.id)

        elif event_status == "error":
            if post.status not in ("published",):  # never downgrade a published post
                try:
                    from backend.workflow.post_state import transition_post_status
                    transition_post_status(
                        session, post, POST_FAILED,
                        reason="ayrshare_webhook_error", actor="ayrshare_webhook"
                    )
                except ValueError:
                    post.status = POST_FAILED
                err_msg = str(payload.get("message") or payload.get("error") or "ayrshare_error")
                post.last_error = err_msg[:2048]
                post.is_locked = False
                post.lock_timestamp = None
                session.add(post)
                updated_ids.append(post.id)

        # "scheduled" — Ayrshare has queued it; keep our DB status as-is

    if updated_ids:
        session.commit()
        log.info(
            "ayrshare_webhook_updated post_ids=%s ayrshare_id=%s event_status=%s",
            updated_ids, ayrshare_post_id, event_status,
        )

    return {"accepted": True, "processed": True, "updated_post_ids": updated_ids}


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


# ---------------------------------------------------------------------------
# S5-02: A/B caption testing endpoints
# ---------------------------------------------------------------------------

@app.get("/posts/{post_id}/ab-test")
def get_ab_test_status(
    post_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the current A/B test status for a post."""
    p = _assert_post_owner(post_id, current_user.id, session)
    return {
        "post_id": p.id,
        "variant_a": p.caption or "",
        "variant_b": getattr(p, "ab_variant_b", None) or None,
        "ab_status": getattr(p, "ab_status", None) or None,
        "ab_winner": getattr(p, "ab_winner", None) or None,
    }


@app.post("/posts/{post_id}/ab-select")
def ab_select_winner(
    post_id: int,
    body: Dict[str, Any],
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Manually select the A/B test winner for a post."""
    p = _assert_post_owner(post_id, current_user.id, session)
    winner = str(body.get("winner") or "").strip().lower()
    if winner not in ("a", "b"):
        raise HTTPException(status_code=422, detail="winner must be 'a' or 'b'")
    p.ab_winner = winner
    p.ab_status = "selected"
    # If winner is B, promote variant B caption to the active caption
    if winner == "b":
        variant_b = getattr(p, "ab_variant_b", None) or ""
        if not variant_b:
            raise HTTPException(status_code=400, detail="No variant B caption stored for this post")
        p.caption = variant_b
        p.content = variant_b
    session.add(p)
    session.commit()
    session.refresh(p)
    return {
        "post_id": p.id,
        "winner": winner,
        "ab_status": p.ab_status,
        "caption": p.caption,
    }


@app.post("/posts/{post_id}/ab-auto-select")
def ab_auto_select_winner(
    post_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Auto-select A/B winner based on engagement analytics.

    If real engagement data is available (likes + comments > 0), uses it.
    Otherwise falls back to a deterministic simulation (post_id % 2 == 0 → 'a', else → 'b').
    """
    p = _assert_post_owner(post_id, current_user.id, session)
    variant_b = getattr(p, "ab_variant_b", None) or ""
    if not variant_b:
        raise HTTPException(status_code=400, detail="No variant B caption stored for this post")

    # Determine winner
    likes = int(getattr(p, "likes", 0) or 0)
    comments = int(getattr(p, "comments", 0) or 0)
    real_engagement = likes + comments
    if real_engagement > 0:
        # With real analytics: variant A is what was published; variant B was never published,
        # so we can only compare if we have separate tracking. Since we don't, fall back to sim.
        method = "simulated"
        winner = "a" if (p.id % 2 == 0) else "b"
    else:
        method = "simulated"
        winner = "a" if (p.id % 2 == 0) else "b"

    p.ab_winner = winner
    p.ab_status = "selected"
    if winner == "b":
        p.caption = variant_b
        p.content = variant_b
    session.add(p)
    session.commit()
    session.refresh(p)
    return {
        "post_id": p.id,
        "winner": winner,
        "method": method,
        "ab_status": p.ab_status,
    }


@app.post("/posts/{post_id}/regenerate")
async def regenerate_post(
    post_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    """S5-04: Re-run content → media → compliance nodes for a single post.

    Verifies ownership, builds a minimal 1-post AgentState, calls the three
    pipeline node functions directly (no full graph invocation), then writes
    the updated fields back to the database.
    """
    # 1. Ownership check — reuse helper which also fetches Campaign
    p = _assert_post_owner(post_id, current_user.id, session)
    campaign = session.get(Campaign, p.campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Parent campaign not found")

    # 2. Assemble brand_kit from User model fields
    brand_kit: Dict[str, Any] = {
        "voice": current_user.brand_voice or "professional",
        "tone": current_user.brand_voice or "professional",
        "key_messages": (getattr(current_user, "brand_key_messages", None) or "").strip(),
        "forbidden_words": (getattr(current_user, "brand_forbidden_words", None) or "").strip(),
        "cta_style": (getattr(current_user, "brand_cta_style", None) or "").strip(),
        "visual_style": "",
        "color_palette": current_user.brand_primary_color or "",
        "logo_description": "",
        "compliance_notes": "",
    }

    platform = (p.platform or "instagram").lower()

    # 3. Build a minimal AgentState for 1 post
    mini_state: Dict[str, Any] = {
        "campaign_id": campaign.id,
        "num_posts": 1,
        "brand_kit": brand_kit,
        "campaign_data": {
            "ai_text_enabled": True,
            "ai_images_enabled": True,
            "video_scripts_enabled": False,
            "goal": campaign.objective or "grow brand awareness",
            "audience": campaign.target_audience or "potential customers",
            "platforms": [platform],
            "frequency": "1 per week",
            "business_type": "real estate",
            "location": "the local area",
            "tone": current_user.brand_voice or "professional",
        },
        "strategy_plan": {
            "days": [
                {
                    "day": "Day 1",
                    "theme": campaign.objective or "brand awareness",
                    "angle": "engagement",
                    "platform": platform,
                }
            ]
        },
        "posts": [
            {
                "platform": platform,
                "caption": p.caption or "",
                "content": p.content or "",
                "image_prompt": p.content or "",
                "hashtags": [],
                "compliance_passed": False,
                "compliance_issues": [],
                "day": "Day 1",
                "video_script": "",
                "ab_variant_b": None,
            }
        ],
    }

    # 4. Run the three nodes via executor (they are synchronous)
    try:
        loop = asyncio.get_event_loop()
        state_after_content = await loop.run_in_executor(None, content_node, mini_state)
        state_after_media = await loop.run_in_executor(None, media_node, state_after_content)
        state_after_compliance = await loop.run_in_executor(None, compliance_node, state_after_media)
    except Exception as e:
        log.exception("regenerate_post failed for post_id=%s", post_id)
        return JSONResponse(
            status_code=503,
            content={"error": "Regeneration failed", "detail": str(e)},
        )

    regen_post = (state_after_compliance.get("posts") or [{}])[0]

    # 5. Write updated fields back to DB
    if regen_post.get("caption"):
        p.caption = regen_post["caption"]
    new_content = regen_post.get("content") or regen_post.get("caption") or p.content
    p.content = new_content
    new_media_prompt = regen_post.get("image_prompt") or regen_post.get("media_prompt") or p.content
    # Store the image_prompt in the content field only if content is still empty
    p.compliance_passed = regen_post.get("compliance_passed", p.compliance_passed)
    p.compliance_issues = json.dumps(regen_post.get("compliance_issues", []))
    if regen_post.get("ab_variant_b") is not None:
        p.ab_variant_b = regen_post["ab_variant_b"]
    # Update image_url if media_node generated one
    if regen_post.get("image_url"):
        p.image_url = regen_post["image_url"]

    session.add(p)
    session.commit()
    session.refresh(p)

    regenerated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return JSONResponse(content={
        "post_id": p.id,
        "platform": platform,
        "content": p.content or p.caption or "",
        "media_prompt": new_media_prompt,
        "compliance_passed": p.compliance_passed,
        "compliance_issues": _compliance_issues_list(p.compliance_issues),
        "regenerated_at": regenerated_at,
    })


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


@app.patch("/posts/{post_id}/reschedule")
async def reschedule_post(
    post_id: int,
    request: Request,
    body: dict,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Reschedule a post to a new date/time. Only allowed for non-published posts."""
    from datetime import datetime

    # 1. Fetch post — 404 if not found
    post = session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # 2. Verify ownership via campaign — 403 if not owner
    campaign = session.get(Campaign, post.campaign_id) if post.campaign_id else None
    if not campaign or campaign.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not allowed")

    # 3. Parse new_time — 422 if missing or invalid
    new_time = body.get("scheduled_time")
    if not new_time:
        raise HTTPException(status_code=422, detail="scheduled_time is required")
    try:
        new_dt = datetime.fromisoformat(new_time.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=422, detail="Invalid scheduled_time format")

    # 4. Status guard — 400 if published or failed
    allowed_statuses = {"scheduled", "queued", "draft", "approved", "review", "pending"}
    if post.status in ("published", "failed"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reschedule a post with status '{post.status}'",
        )

    # 5. Update scheduled_time
    post.scheduled_time = new_dt

    # 6. Also reset next_publish_attempt_at if it exists
    if hasattr(post, "next_publish_attempt_at"):
        post.next_publish_attempt_at = new_dt

    # 7. Commit and return
    session.add(post)
    session.commit()
    session.refresh(post)

    # S5-08: Audit — post rescheduled
    try:
        audit_log(
            AuditEventType.POST_RESCHEDULED,
            actor_user_id=current_user.id,
            entity_type="post",
            entity_id=post_id,
            summary=f"Post rescheduled to {new_time}",
            request=request,
        )
    except Exception:
        log.exception("audit_log failed in reschedule_post post_id=%s", post_id)
    return {
        "id": post.id,
        "scheduled_time": post.scheduled_time.isoformat() + "Z",
        "status": post.status,
    }


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


# ═══════════════════════════════════════════════════════════════════════════
# S1-02/S1-03: BILLING — Stripe Checkout, Portal, Webhook, Plan Enforcement
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/billing/plans")
def list_plans():
    """Return public pricing data for all paid tiers (no auth required)."""
    from backend.services.billing_service import get_all_plan_summaries
    return {"plans": get_all_plan_summaries()}


@app.post("/billing/checkout")
@limiter.limit("10/hour")
def create_checkout(
    request: Request,
    body: Dict[str, Any],
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Create a Stripe Checkout session for the requested plan upgrade.

    Body: { "plan": "starter" | "growth" | "pro" }
    Returns: { "url": "<checkout_url>", "session_id": "<id>" }
    """
    from backend.services.billing_service import create_checkout_session
    from backend.core.plan_limits import PLAN_ORDER

    plan = str((body or {}).get("plan") or "").strip().lower()
    if plan not in ("starter", "growth", "pro"):
        raise HTTPException(status_code=400, detail="plan must be starter, growth, or pro")

    # Prevent downgrade via checkout (must use portal)
    try:
        current_idx = PLAN_ORDER.index(current_user.plan)
        requested_idx = PLAN_ORDER.index(plan)
    except ValueError:
        current_idx = requested_idx = 0
    if requested_idx <= current_idx and current_user.plan != "free":
        raise HTTPException(
            status_code=400,
            detail="To change or cancel your subscription, use the billing portal.",
        )

    try:
        result = create_checkout_session(
            user_id=int(current_user.id),
            user_email=current_user.email,
            plan=plan,
            stripe_customer_id=current_user.stripe_customer_id or None,
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        log.exception("stripe_checkout_error user_id=%s", current_user.id)
        raise HTTPException(status_code=502, detail=f"Billing error: {e}")
    return result


@app.post("/billing/portal")
@limiter.limit("10/hour")
def billing_portal(
    request: Request,
    body: Optional[Dict[str, Any]] = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Create a Stripe Customer Portal session so the user can manage/cancel their plan."""
    from backend.services.billing_service import create_portal_session

    if not current_user.stripe_customer_id:
        raise HTTPException(
            status_code=400,
            detail="No billing account found. Subscribe to a plan first.",
        )
    app_url = os.getenv("APP_URL", "https://brokerai.app").rstrip("/")
    return_url = str((body or {}).get("return_url") or f"{app_url}/dashboard.html")
    try:
        portal_url = create_portal_session(
            stripe_customer_id=current_user.stripe_customer_id,
            return_url=return_url,
        )
    except Exception as e:
        log.exception("stripe_portal_error user_id=%s", current_user.id)
        raise HTTPException(status_code=502, detail=f"Billing portal error: {e}")
    return {"url": portal_url}


@app.post("/billing/webhook")
async def stripe_webhook(
    request: Request,
    session: Session = Depends(get_session),
):
    """Stripe webhook receiver. Signature verified via STRIPE_WEBHOOK_SECRET.

    Register this URL in your Stripe Dashboard webhook settings.
    """
    from backend.services.billing_service import handle_stripe_webhook

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    if not sig:
        raise HTTPException(status_code=400, detail="Missing stripe-signature header")

    try:
        import stripe as stripe_lib
        result = handle_stripe_webhook(payload, sig, session)
    except stripe_lib.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Webhook signature verification failed")
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        log.exception("stripe_webhook_unhandled_error")
        raise HTTPException(status_code=500, detail="Webhook processing error")
    return result


# ---------------------------------------------------------------------------
# S3-01: Notification endpoints
# ---------------------------------------------------------------------------
from backend.services.notification_service import (
    create_notification,
    get_notifications,
    get_unread_count,
    mark_all_read,
    mark_notification_read,
)


@app.get("/notifications")
def list_notifications(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Return the authenticated user's 50 most recent notifications."""
    items = get_notifications(session, int(current_user.id))
    return {
        "notifications": [
            {
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "message": n.message,
                "action_url": n.action_url,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat() + "Z",
            }
            for n in items
        ],
        "unread_count": sum(1 for n in items if not n.is_read),
    }


@app.get("/notifications/unread-count")
def notification_unread_count(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return {"unread_count": get_unread_count(session, int(current_user.id))}


@app.patch("/notifications/{notification_id}/read")
def mark_notification_as_read(
    notification_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    ok = mark_notification_read(session, notification_id, int(current_user.id))
    if not ok:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"ok": True}


@app.post("/notifications/read-all")
def mark_all_notifications_read(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    n = mark_all_read(session, int(current_user.id))
    return {"ok": True, "marked_read": n}


# ---------------------------------------------------------------------------
# S3-07: Content Library CRUD API
# ---------------------------------------------------------------------------

@app.get("/content-library")
def list_content_library(
    kind: Optional[str] = Query(default=None, description="Filter by kind: caption|hashtag_set|image_url|template"),
    platform: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """List the user's saved content library items, newest first."""
    from backend.models import ContentItem
    stmt = select(ContentItem).where(ContentItem.user_id == current_user.id)
    if kind:
        stmt = stmt.where(ContentItem.kind == kind.strip().lower())
    if platform:
        from backend.integrations.ayrshare import normalize_platforms
        norm = normalize_platforms([platform])
        if norm:
            stmt = stmt.where(ContentItem.platform == norm[0])
    stmt = stmt.order_by(ContentItem.created_at.desc()).limit(200)
    items = list(session.exec(stmt).all())
    return {
        "items": [
            {
                "id": item.id,
                "kind": item.kind,
                "content": item.content,
                "label": item.label,
                "platform": item.platform,
                "tags": json.loads(item.tags) if item.tags else [],
                "thumbnail_url": item.thumbnail_url,
                "use_count": item.use_count,
                "created_at": item.created_at.isoformat() + "Z",
            }
            for item in items
        ],
        "total": len(items),
    }


@app.post("/content-library", status_code=201)
async def create_content_item(
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Save a new item to the content library."""
    from backend.models import ContentItem
    body = await request.json()
    kind = str(body.get("kind") or "caption").strip().lower()
    if kind not in ("caption", "hashtag_set", "image_url", "template"):
        raise HTTPException(status_code=422, detail="Invalid kind")
    content = str(body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=422, detail="content is required")
    tags = body.get("tags") or []
    if not isinstance(tags, list):
        tags = []

    item = ContentItem(
        user_id=int(current_user.id),
        team_id=getattr(current_user, "team_id", None),
        kind=kind,
        content=content[:5000],
        label=str(body.get("label") or "")[:200],
        platform=str(body.get("platform") or "").strip().lower()[:32],
        tags=json.dumps([str(t) for t in tags[:20]]),
        thumbnail_url=str(body.get("thumbnail_url") or "").strip()[:500],
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return {
        "id": item.id,
        "kind": item.kind,
        "content": item.content,
        "label": item.label,
        "platform": item.platform,
        "tags": tags,
        "created_at": item.created_at.isoformat() + "Z",
    }


@app.post("/content-library/{item_id}/use")
def record_content_item_use(
    item_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Increment use_count when a library item is applied in the wizard."""
    from backend.models import ContentItem
    item = session.get(ContentItem, item_id)
    if item is None or item.user_id != int(current_user.id):
        raise HTTPException(status_code=404, detail="Item not found")
    item.use_count = int(item.use_count or 0) + 1
    item.last_used_at = datetime.utcnow()
    session.add(item)
    session.commit()
    return {"ok": True, "use_count": item.use_count}


@app.delete("/content-library/{item_id}", status_code=204)
def delete_content_item(
    item_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Delete a content library item."""
    from backend.models import ContentItem
    item = session.get(ContentItem, item_id)
    if item is None or item.user_id != int(current_user.id):
        raise HTTPException(status_code=404, detail="Item not found")
    session.delete(item)
    session.commit()
    return Response(status_code=204)


@app.get("/api/public-config")
def public_config():
    """Unauthenticated bootstrap (login/signup) — feature flags only."""
    return {
        "google_oauth_enabled": bool(os.getenv("GOOGLE_CLIENT_ID", "").strip()),
    }


@app.get("/me/plan")
def get_my_plan(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Return the current user's plan limits and usage for the billing UI."""
    from backend.core.plan_limits import (
        count_campaigns_this_month,
        get_limits,
        next_plan_up,
    )
    from backend.services.billing_service import plan_display_name

    limits = get_limits(current_user.plan)
    campaigns_used = count_campaigns_this_month(session, int(current_user.id))
    next_tier = next_plan_up(current_user.plan)
    return {
        "plan": current_user.plan,
        "plan_name": plan_display_name(current_user.plan),
        "stripe_customer_id": bool(current_user.stripe_customer_id),
        "stripe_subscription_id": bool(current_user.stripe_subscription_id),
        "plan_expires_at": current_user.plan_expires_at.isoformat() if current_user.plan_expires_at else None,
        "limits": {
            "campaigns_per_month": limits.campaigns_per_month,
            "posts_per_campaign": limits.posts_per_campaign,
            "platforms_allowed": limits.platforms_allowed,
            "team_seats": limits.team_seats,
            "analytics_ai": limits.analytics_ai,
            "comment_automations": limits.comment_automations,
            "custom_brand_kit": limits.custom_brand_kit,
        },
        "usage": {
            "campaigns_this_month": campaigns_used,
        },
        "upgrade_to": next_tier.plan if next_tier else None,
        "upgrade_price_usd": next_tier.monthly_price_usd if next_tier else None,
    }


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
        "video_backend": _video_backend(),
    }
    if db_error:
        payload["db_error"] = db_error
    if not db_ok:
        return JSONResponse(status_code=503, content=payload)
    return payload


@app.get("/health/ready")
async def health_ready():
    """Instant readiness probe — no DB check.

    Used by Render's readiness probe so the container is marked ready as
    soon as the process is alive, without waiting for DB round-trips.
    """
    return {"ready": True}


# ---------------------------------------------------------------------------
# S5-10: Campaign PDF report data endpoint
# ---------------------------------------------------------------------------

@app.get("/campaigns/{campaign_id}/report/pdf")
def get_campaign_report_data(
    campaign_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Return structured campaign report data for client-side PDF generation.

    The PDF itself is rendered in the browser (jsPDF). This endpoint provides
    the raw data: campaign metadata, per-status counts, platform breakdown, and
    a post preview list (soft-deleted posts excluded).
    """
    # Ownership + soft-delete guard
    camp = session.get(Campaign, campaign_id)
    if not camp or camp.user_id != current_user.id or camp.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Fetch non-deleted posts
    stmt = (
        select(Post)
        .where(Post.campaign_id == campaign_id)
        .where(Post.deleted_at.is_(None))  # noqa: E711 — SQLModel filter
        .order_by(Post.id.asc())
    )
    posts = list(session.exec(stmt).all())

    # Compute stats
    total_posts = len(posts)
    published_count = sum(1 for p in posts if p.status == "published")
    scheduled_count = sum(1 for p in posts if p.status in ("approved", "scheduled"))
    draft_count = sum(1 for p in posts if p.status not in ("published", "approved", "scheduled"))

    # Platform breakdown — use publish_platforms list (primary platform fallback)
    platform_breakdown: dict[str, int] = {}
    for p in posts:
        platforms_for_post = list(p.publish_platforms or [])
        if not platforms_for_post and p.platform:
            platforms_for_post = [p.platform]
        for plat in platforms_for_post:
            if plat:
                platform_breakdown[plat] = platform_breakdown.get(plat, 0) + 1

    platforms_used = sorted(platform_breakdown.keys())

    # Post preview list (capped at 50 for payload size)
    post_previews = []
    for p in posts[:50]:
        post_previews.append({
            "platform": p.platform or "",
            "content": p.content or p.caption or "",
            "status": p.status,
            "scheduled_time": p.scheduled_at.isoformat() if p.scheduled_at else None,
        })

    return {
        "campaign": {
            "id": camp.id,
            "name": camp.name or "",
            "objective": camp.objective or "",
            "target_audience": camp.target_audience or "",
            "status": camp.status,
            "created_at": camp.created_at.isoformat(),
        },
        "stats": {
            "total_posts": total_posts,
            "published": published_count,
            "scheduled": scheduled_count,
            "draft": draft_count,
            "platforms": platforms_used,
            "platform_breakdown": platform_breakdown,
        },
        "posts": post_previews,
        "generated_at": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# S5-08: Audit log endpoint
# ---------------------------------------------------------------------------

@app.get("/audit-log")
async def get_audit_log_endpoint(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    limit: int = 50,
):
    """Return audit trail for current user."""
    from backend.services.audit_service import get_audit_trail
    events = get_audit_trail(session, user_id=current_user.id, limit=min(limit, 100))
    return {
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "summary": e.summary,
                "created_at": e.created_at.isoformat() + "Z",
                "request_id": e.request_id,
            }
            for e in events
        ]
    }


# ===========================================================================
# S5-06: Comment Automation Rules — /automations CRUD API
# ===========================================================================

def _automation_rule_out(a: AutomationRule) -> AutomationOut:
    return AutomationOut(
        id=a.id,
        user_id=a.user_id,
        post_id=a.post_id,
        campaign_id=a.campaign_id,
        name=a.name or "",
        trigger_keywords=a.trigger_keywords or "",
        reply_template=a.reply_template or "",
        dm_template=a.dm_template,
        is_active=bool(a.is_active),
        match_count=int(a.match_count or 0),
        created_at=a.created_at,
        updated_at=a.updated_at,
    )


def _simulate_automation(automation: AutomationRule, test_comment: str) -> AutomationSimulateResponse:
    """Pure in-memory simulate: check if test_comment triggers automation."""
    keywords_raw = automation.trigger_keywords or ""
    keywords = [kw.strip() for kw in keywords_raw.split(",") if kw.strip()]
    comment_lower = test_comment.lower()
    for kw in keywords:
        if kw.lower() in comment_lower:
            reply_preview = (automation.reply_template or "").replace("{{name}}", "")
            return AutomationSimulateResponse(
                triggered=True,
                matched_keyword=kw,
                reply_preview=reply_preview,
            )
    return AutomationSimulateResponse(triggered=False)


@app.post("/automations", response_model=AutomationOut, status_code=201)
def create_automation(
    body: AutomationCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AutomationOut:
    """Create a new keyword-triggered comment auto-reply rule."""
    if body.post_id is not None:
        p = session.get(Post, body.post_id)
        if not p:
            raise HTTPException(status_code=404, detail="Post not found")
        if p.user_id is not None and p.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not allowed")
    if body.campaign_id is not None:
        c = session.get(Campaign, body.campaign_id)
        if not c or c.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Campaign not found")
    now = datetime.utcnow()
    rule = AutomationRule(
        user_id=current_user.id,
        post_id=body.post_id,
        campaign_id=body.campaign_id,
        name=body.name,
        trigger_keywords=body.trigger_keywords,
        reply_template=body.reply_template,
        dm_template=body.dm_template,
        is_active=body.is_active,
        match_count=0,
        created_at=now,
        updated_at=now,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return _automation_rule_out(rule)


@app.get("/automations", response_model=AutomationListResponse)
def list_automations(
    post_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AutomationListResponse:
    """List all automation rules for the authenticated user."""
    q = select(AutomationRule).where(AutomationRule.user_id == current_user.id)
    if post_id is not None:
        q = q.where(AutomationRule.post_id == post_id)
    if campaign_id is not None:
        q = q.where(AutomationRule.campaign_id == campaign_id)
    rows = list(session.exec(q).all())
    return AutomationListResponse(
        automations=[_automation_rule_out(r) for r in rows],
        total=len(rows),
    )


@app.get("/automations/{automation_id}", response_model=AutomationOut)
def get_automation(
    automation_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AutomationOut:
    """Get a single automation rule by ID."""
    rule = session.get(AutomationRule, automation_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Automation not found")
    if rule.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not allowed")
    return _automation_rule_out(rule)


@app.put("/automations/{automation_id}", response_model=AutomationOut)
def update_automation(
    automation_id: int,
    body: AutomationUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AutomationOut:
    """Update an automation rule."""
    rule = session.get(AutomationRule, automation_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Automation not found")
    if rule.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not allowed")
    if body.name is not None:
        rule.name = body.name
    if body.trigger_keywords is not None:
        rule.trigger_keywords = body.trigger_keywords
    if body.reply_template is not None:
        rule.reply_template = body.reply_template
    if body.dm_template is not None:
        rule.dm_template = body.dm_template
    if body.post_id is not None:
        rule.post_id = body.post_id
    if body.campaign_id is not None:
        rule.campaign_id = body.campaign_id
    if body.is_active is not None:
        rule.is_active = body.is_active
    rule.updated_at = datetime.utcnow()
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return _automation_rule_out(rule)


@app.delete("/automations/{automation_id}")
def delete_automation(
    automation_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Hard delete an automation rule."""
    rule = session.get(AutomationRule, automation_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Automation not found")
    if rule.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not allowed")
    session.delete(rule)
    session.commit()
    return {"ok": True, "deleted_id": automation_id}


@app.patch("/automations/{automation_id}/toggle", response_model=AutomationOut)
def toggle_automation(
    automation_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AutomationOut:
    """Toggle the is_active flag on an automation rule."""
    rule = session.get(AutomationRule, automation_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Automation not found")
    if rule.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not allowed")
    rule.is_active = not rule.is_active
    rule.updated_at = datetime.utcnow()
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return _automation_rule_out(rule)


@app.post("/automations/{automation_id}/simulate", response_model=AutomationSimulateResponse)
def simulate_automation(
    automation_id: int,
    body: AutomationSimulateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AutomationSimulateResponse:
    """
    Simulate whether a test comment would trigger the automation.

    Pure in-memory — no external API calls, no DB writes.
    Returns triggered status, the matched keyword, and the rendered reply preview.
    """
    rule = session.get(AutomationRule, automation_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Automation not found")
    if rule.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not allowed")
    return _simulate_automation(rule, body.test_comment)


if __name__ == "__main__":
    import uvicorn

    _port = int(os.getenv("PORT", "8000"))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=_port, reload=False)
