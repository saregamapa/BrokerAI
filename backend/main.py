import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

import backend.env_loader  # noqa: F401 — loads project root .env before agent imports

from backend.core.logger import configure_logging, get_logger
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from backend.agents.analytics_insights import run_campaign_insights
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
    publish_post,
)
from backend.models import Campaign, Post, User
from backend.schemas import (
    AnalyticsOut,
    ApproveCampaignRequest,
    CampaignInsightsOut,
    CampaignDetailOut,
    CampaignOut,
    CheckComplianceRequest,
    CheckComplianceResponse,
    ConnectSocialResponse,
    GenerateCampaignRequest,
    GenerateCampaignResponse,
    LoginRequest,
    PostAnalyticsOut,
    PostOut,
    SignupRequest,
    SocialConnectedCallbackResponse,
    SocialStatusResponse,
    TokenResponse,
    UpdatePostRequest,
    UserOut,
)
from backend.services.ayrshare_service import (
    AyrshareServiceError,
    create_ayrshare_profile,
    fetch_active_social_accounts,
    generate_social_connect_url,
    has_linked_target_platform,
)
from backend.services.analytics import fetch_post_analytics, get_analytics_payload

load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # refresh if needed

configure_logging()
log = get_logger("brokerai")

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


def _sync_user_social_from_ayrshare(session: Session, user: User) -> None:
    """Refresh social_connected from Ayrshare GET /user (Profile-Key)."""
    pk = (user.ayrshare_profile_key or "").strip()
    if not pk:
        return
    active = fetch_active_social_accounts(pk)
    if active is None:
        return
    user.social_connected = has_linked_target_platform(active)
    session.add(user)
    session.commit()


def _require_social_ready(session: Session, user_id: int) -> None:
    u = session.get(User, user_id)
    if not u:
        raise HTTPException(status_code=401, detail="Not authenticated")
    _sync_user_social_from_ayrshare(session, u)
    session.refresh(u)
    if not u.social_connected:
        raise HTTPException(
            status_code=403,
            detail="Please connect your social accounts first",
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


def _post_to_out(row: Post, day: Optional[str] = None) -> PostOut:
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
    )


async def _publish_due_posts() -> None:
    now = datetime.utcnow()
    with Session(engine) as session:
        # Only pick up "approved" posts — not "publishing" (avoids double-publish)
        stmt = select(Post).where(
            Post.status == "approved",
            Post.scheduled_at <= now,
            Post.publish_attempts < 3,
        )
        rows = session.exec(stmt).all()
        for row in rows:
            if row.user_id is None:
                continue

            # Atomically mark as "publishing" to prevent concurrent scheduler
            # ticks from picking up the same post
            row.status = "publishing"
            row.publish_attempts = (row.publish_attempts or 0) + 1
            session.add(row)
            session.commit()

            tags = _hashtags_to_list(row.hashtags)
            tail = " ".join(tags)
            full_caption = row.caption if not tail else f"{row.caption}\n\n{tail}"
            pl = coerce_ayrshare_platforms(row.publish_platforms)
            image_url = (row.image_url or "").strip()
            media_urls = (
                [image_url]
                if image_url.lower().startswith(("http://", "https://"))
                else None
            )

            owner = session.get(User, row.user_id)
            profile_key = (
                (owner.ayrshare_profile_key or "").strip() if owner is not None else ""
            )
            last_result = await publish_post(
                full_caption,
                pl,
                media_urls=media_urls,
                profile_key=profile_key or None,
            )
            row.platform_response = platform_response_json(last_result or {})

            if last_result.get("ok"):
                row.status = "published"
                row.published_at = datetime.utcnow()
                log.info("Published post %s for user %s", row.id, row.user_id)
                try:
                    await fetch_post_analytics(session, row.id, row.user_id)
                except Exception:
                    log.warning(
                        "post analytics prefetch failed post_id=%s (non-fatal)",
                        row.id,
                        exc_info=True,
                    )
            elif row.publish_attempts >= 3:
                row.status = "publish_failed"
                log.warning(
                    "Publish failed post %s user %s after %s attempts — %s",
                    row.id, row.user_id, row.publish_attempts,
                    (last_result or {}).get("body"),
                )
            else:
                # Put back to approved for retry on next scheduler tick
                row.status = "approved"
                log.info(
                    "Publish attempt %s failed for post %s, will retry",
                    row.publish_attempts, row.id,
                )
            session.add(row)
            session.commit()


async def _scheduler_loop() -> None:
    while True:
        try:
            await _publish_due_posts()
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

# CORS: Allow all origins in dev, restrict in production via ALLOWED_ORIGINS env var
_allowed_origins = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _allowed_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/")
async def serve_index():
    return FileResponse(BASE_DIR / "frontend" / "index.html")


@app.get("/wizard.html")
async def serve_wizard():
    return FileResponse(BASE_DIR / "frontend" / "wizard.html")


@app.get("/review.html")
async def serve_review():
    return FileResponse(BASE_DIR / "frontend" / "review.html")


@app.get("/dashboard.html")
async def serve_dashboard():
    return FileResponse(BASE_DIR / "frontend" / "dashboard.html")


@app.get("/login.html")
async def serve_login():
    return FileResponse(BASE_DIR / "frontend" / "login.html")


@app.get("/signup.html")
async def serve_signup():
    return FileResponse(BASE_DIR / "frontend" / "signup.html")


@app.get("/connect.html")
async def serve_connect():
    return FileResponse(BASE_DIR / "frontend" / "connect.html")


@app.post("/connect-social", response_model=ConnectSocialResponse)
async def connect_social(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Ensure an Ayrshare User Profile exists and return JWT SSO URL to link networks."""
    user = session.get(User, current_user.id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        if not (user.ayrshare_profile_key or "").strip():
            pk = await asyncio.to_thread(
                create_ayrshare_profile, user.id, user.email
            )
            user.ayrshare_profile_key = pk
            session.add(user)
            session.commit()
            session.refresh(user)
        redirect = _connect_redirect_url()
        url = await asyncio.to_thread(
            generate_social_connect_url,
            (user.ayrshare_profile_key or "").strip(),
            redirect,
        )
    except AyrshareServiceError as e:
        log.warning(
            "connect-social failed user_id=%s: %s",
            current_user.id,
            e.message,
        )
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    return ConnectSocialResponse(connect_url=url)


@app.get("/social-status", response_model=SocialStatusResponse)
def social_status(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    user = session.get(User, current_user.id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    _sync_user_social_from_ayrshare(session, user)
    session.refresh(user)
    return SocialStatusResponse(
        connected=bool(user.social_connected),
        profile_key=user.ayrshare_profile_key or None,
    )


@app.post("/social-connected-callback", response_model=SocialConnectedCallbackResponse)
def social_connected_callback(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Re-sync linked networks from Ayrshare after the user finishes SSO linking."""
    user = session.get(User, current_user.id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    _sync_user_social_from_ayrshare(session, user)
    session.refresh(user)
    return SocialConnectedCallbackResponse(
        ok=True,
        connected=bool(user.social_connected),
    )


@app.post("/signup", response_model=TokenResponse)
def signup(body: SignupRequest, session: Session = Depends(get_session)):
    email = body.email.strip().lower()
    if get_user_by_email(session, email):
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(email=email, password_hash=hash_password(body.password))
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=400, detail="Email already registered")
    session.refresh(user)
    return TokenResponse(access_token=create_access_token(user.id))


@app.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, session: Session = Depends(get_session)):
    email = body.email.strip().lower()
    user = get_user_by_email(session, email)
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenResponse(access_token=create_access_token(user.id))


@app.get("/me", response_model=UserOut)
def me(
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    u = session.get(User, user.id)
    if u is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return UserOut(
        id=u.id,
        email=u.email,
        social_connected=bool(getattr(u, "social_connected", False)),
    )


PLAN_LIMITS = {
    "free": 2,      # 2 campaigns max
    "pro": 15,      # 15 campaigns/month
    "agency": 9999, # effectively unlimited
}


@app.post("/generate-campaign", response_model=GenerateCampaignResponse)
async def generate_campaign(
    body: GenerateCampaignRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    _require_social_ready(session, current_user.id)

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

    utc_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    gkey = (current_user.id, utc_day)
    used = _daily_generate_count.get(gkey, 0)
    if used >= _MAX_CAMPAIGNS_PER_USER_PER_DAY:
        raise HTTPException(
            status_code=429,
            detail=f"Daily campaign generation limit reached ({_MAX_CAMPAIGNS_PER_USER_PER_DAY} per day). Try again tomorrow.",
        )

    camp = Campaign(
        user_id=current_user.id,
        status="draft",
        graph_thread_id="",
        facebook_url=body.facebook_url or "",
        instagram_url=body.instagram_url or "",
        linkedin_url=body.linkedin_url or "",
    )
    session.add(camp)
    session.commit()
    session.refresh(camp)
    thread_id = f"campaign-{camp.id}"
    camp.graph_thread_id = thread_id
    camp.updated_at = datetime.utcnow()
    session.add(camp)
    session.commit()

    initial = {
        "user_id": current_user.id,
        "campaign_id": camp.id,
        "approved": False,
        "campaign_data": body.model_dump(),
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
    current_user: User = Depends(get_current_user),
):
    _require_social_ready(session, current_user.id)

    camp = session.get(Campaign, body.campaign_id)
    if not camp or camp.user_id != current_user.id:
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
    current_user: User = Depends(get_current_user),
):
    row = session.get(Post, post_id)
    if not row or row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Post not found")
    row.status = "approved"
    row.scheduled_at = row.scheduled_at or datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return _post_to_out(row)


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


@app.get("/stats")
async def stats(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Extended stats for dashboards (includes plan + breakdown fields)."""
    payload = get_analytics_payload(session, current_user.id)
    stmt = select(Post).where(Post.user_id == current_user.id)
    rows = session.exec(stmt).all()
    scheduled = sum(1 for r in rows if r.status in ("approved", "publishing"))
    pending = sum(1 for r in rows if r.status == "pending_approval")
    attempted = payload["posts_published"] + payload["posts_failed"]
    success_rate_pct = (
        round((payload["posts_published"] / attempted) * 100, 1) if attempted > 0 else None
    )
    return {
        "total_posts": payload["total_posts"],
        "published": payload["posts_published"],
        "failed": payload["posts_failed"],
        "scheduled": scheduled,
        "pending_approval": pending,
        "success_rate_pct": success_rate_pct,
        "last_published_at": payload["last_published_at"],
        "total_campaigns": payload["total_campaigns"],
        "plan": current_user.plan,
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "ok": True,
        "openai_configured": bool(_openai_api_key()),
    }


if __name__ == "__main__":
    import uvicorn

    _port = int(os.getenv("PORT", "8000"))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=_port, reload=False)
