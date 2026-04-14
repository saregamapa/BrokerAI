import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import backend.env_loader  # noqa: F401 — ensure project root .env loaded if agents imported first

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from backend.agents.errors import CampaignPipelineError, OpenAINotConfiguredError
from backend.agents.state import AgentState
from backend.core.logger import get_logger
from backend.services.ai_media_service import (
    build_image_prompt,
    generate_image,
    generate_video_script,
    video_script_to_storage_value,
)
from backend.db import engine
from backend.integrations.ayrshare import coerce_ayrshare_platforms, normalize_platforms
from backend.models import Campaign, Post
from backend.workflow.post_state import POST_APPROVED, POST_REVIEW, transition_post_status

log = get_logger("brokerai.agents")

DAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]

# Default fallback when frequency is unknown.
CAMPAIGN_POST_COUNT = 7


def _num_posts_for_frequency(freq: str) -> int:
    """Map a human-readable posting frequency to a post count."""
    f = (freq or "").lower()
    if "daily" in f or "every day" in f:
        return 7
    if "5" in f and "week" in f:
        return 5
    if "3" in f and "week" in f:
        return 3
    if "1" in f and "week" in f:
        return 1
    return CAMPAIGN_POST_COUNT


def _days_for_frequency(freq: str) -> List[str]:
    """Return day-name labels matching the post count for this frequency."""
    n = _num_posts_for_frequency(freq)
    if n >= 7:
        return DAYS[:]
    if n == 5:
        return DAYS[:5]   # Mon–Fri
    if n == 3:
        return ["Monday", "Wednesday", "Friday"]
    if n == 1:
        return ["Day 1"]
    return DAYS[:n]

_BAD_PLACEHOLDER_KEYS = frozenset(
    {"your_key_here", "sk-your-key-here", "sk-proj-replace-me", "replace_me",
     "your_openai_key_here", "your-openai-key-here"}
)


class DayPlan(BaseModel):
    day: str
    theme: str
    angle: str


class StrategyPlan(BaseModel):
    days: List[DayPlan]


class OnePost(BaseModel):
    day: str
    caption: str
    hashtags: List[str] = Field(default_factory=list)
    image_prompt: str = ""
    video_script: str = ""


class ContentPack(BaseModel):
    posts: List[OnePost]


class ComplianceLLM(BaseModel):
    passed: bool
    issues: List[str] = Field(default_factory=list)
    fixed_caption: str = ""


def _openai_api_key() -> str:
    k = os.getenv("OPENAI_API_KEY", "").strip()
    if not k:
        return ""
    low = k.lower().replace(" ", "")
    if k in _BAD_PLACEHOLDER_KEYS or low in _BAD_PLACEHOLDER_KEYS:
        return ""
    return k


def _campaign_data(state: AgentState) -> Dict[str, Any]:
    return state.get("campaign_data") or {}


def _ai_text_on(state: AgentState) -> bool:
    return bool(_campaign_data(state).get("ai_text_enabled", True))


def _ai_images_on(state: AgentState) -> bool:
    return bool(_campaign_data(state).get("ai_images_enabled", True))


def _video_scripts_on(state: AgentState) -> bool:
    return bool(_campaign_data(state).get("video_scripts_enabled", True))


def _social_presence_prompt_block(data: Dict[str, Any]) -> str:
    """Human-readable block for content prompts: URLs inform tone; empty → generic."""
    fb = (data.get("facebook_url") or "").strip()
    ig = (data.get("instagram_url") or "").strip()
    li = (data.get("linkedin_url") or "").strip()
    if not fb and not ig and not li:
        return (
            "User social presence: No profile URLs were provided. "
            "Use a professional, warm, trustworthy brand tone that works across "
            "Facebook, Instagram, and LinkedIn; prioritize local relevance and the stated goal and audience."
        )
    return (
        "User social presence:\n"
        f"Facebook: {fb or '(not provided)'}\n"
        f"Instagram: {ig or '(not provided)'}\n"
        f"LinkedIn: {li or '(not provided)'}\n\n"
        "Generate content aligned with their brand tone and audience. "
        "Infer voice and depth from the channel mix (e.g. LinkedIn more professional, Instagram more visual "
        "and story-led where it fits). Do not claim you browsed the profiles; use URLs only as context."
    )


def _llm(key: str) -> ChatOpenAI:
    return ChatOpenAI(model="gpt-4o-mini", temperature=0.6, api_key=key)


def _parse_start(s: Any) -> date:
    if not s:
        return datetime.utcnow().date()
    try:
        return datetime.fromisoformat(str(s)[:10]).date()
    except Exception:
        return datetime.utcnow().date()


def _schedule_offsets(freq: str) -> List[int]:
    """Return day-offsets from start_date for each post, matching the post count."""
    n = _num_posts_for_frequency(freq)
    if n >= 7:
        return list(range(7))       # daily: 0,1,2,3,4,5,6
    if n == 5:
        return [0, 1, 2, 3, 4]     # 5/wk: Mon–Fri
    if n == 3:
        return [0, 2, 4]            # 3/wk: Mon, Wed, Fri
    if n == 1:
        return [0]                  # weekly: start date only
    return list(range(n))


_STRATEGY_SYSTEM = """\
You are a senior social media strategist who has managed campaigns \
for businesses and brands across every industry. You understand what content drives \
engagement, builds trust, and generates leads on social media.

Key principles you follow:
- Mix content types: educational, social proof, community, behind-the-scenes, calls-to-action
- Never post the same type of content two days in a row
- Local relevance beats generic content — always tie content to the specific market and business
- Each day should have a clear PURPOSE (educate, engage, convert, nurture)
- Weekend content is lighter and more personal; weekday content is more professional

Content Mix AI (STRICT RATIOS across the full plan):
- 40% Value / Tips (educate the audience; actionable advice)
- 30% Listings / Product Showcase (the offer, specific properties or services)
- 20% Engagement (polls, questions, community spotlights)
- 10% Promotions (limited-time offers, CTAs, open-house invites, bookings)

Distribute angles so the final set honours these ratios as closely as integer \
rounding allows for the requested post count. Round 0.5 UP for the largest bucket first.
"""


def strategy_node(state: AgentState) -> Dict[str, Any]:
    cid = state.get("campaign_id")
    data = _campaign_data(state)
    key = _openai_api_key()
    log.info(
        "[agent:strategy] campaign_id=%s goal=%s ai_text=%s key_present=%s",
        cid,
        data.get("goal"),
        _ai_text_on(state),
        bool(key),
    )
    if not _ai_text_on(state):
        raise CampaignPipelineError(
            "AI strategy is disabled. Enable AI captions & strategy in the wizard."
        )
    if not key:
        raise OpenAINotConfiguredError(
            "OPENAI_API_KEY is required for strategy generation."
        )
    freq = data.get("frequency", "3 per week")
    num_posts = _num_posts_for_frequency(freq)
    day_labels = _days_for_frequency(freq)
    day_list_str = ", ".join(day_labels)

    try:
        llm = _llm(key).with_structured_output(StrategyPlan)
        location = data.get("location", "the local area")
        audience = data.get("audience") or "potential customers in the local area"
        goal = data.get("goal", "grow brand awareness")
        biz = data.get("business_type", "small business")
        msg = (
            f"Build a {num_posts}-post social media content plan for a {biz} in {location}.\n\n"
            f"PRIMARY GOAL: {goal}\n"
            f"TARGET AUDIENCE: {audience}\n\n"
            "Requirements:\n"
            f"- Output exactly {num_posts} posts using these day labels: {day_list_str}\n"
            "- Each day needs: theme (2-4 words) and angle (one sentence describing the specific post idea)\n"
            "- Vary content types: industry insight, social proof/testimonial, "
            "community spotlight, educational tip, behind-the-scenes, product/service highlight, personal/lifestyle\n"
            f"- Make angles SPECIFIC to {location} — reference neighborhoods, local landmarks, "
            "local culture, or seasonal relevance when possible\n"
            "- At least one post should include a clear call-to-action\n"
            "- All content must be inclusive and welcoming to all audiences"
        )
        plan: StrategyPlan = llm.invoke(
            [
                SystemMessage(content=_STRATEGY_SYSTEM),
                HumanMessage(content=msg),
            ]
        )
    except CampaignPipelineError:
        raise
    except Exception as e:
        log.exception("[agent:strategy] OpenAI structured output failed")
        raise CampaignPipelineError(f"Strategy generation failed: {e}") from e

    if len(plan.days) != num_posts:
        raise CampaignPipelineError(
            f"Strategy must return exactly {num_posts} posts; got {len(plan.days)}."
        )
    return {
        "num_posts": num_posts,
        "strategy_plan": plan.model_dump(),
        "step_log": [f"strategy: OpenAI plan ({num_posts} posts, freq={freq})"],
    }


_CONTENT_SYSTEM = """\
You are a top-performing social media copywriter. Your captions \
consistently get high engagement because you follow these rules:

CAPTION RULES:
1. HOOK FIRST: Every caption starts with an attention-grabbing first line \
(question, bold statement, surprising stat, or pattern interrupt). The first \
line must make someone stop scrolling.
2. LOCAL FLAVOR: Reference the specific city, neighborhoods, local landmarks, \
or relevant context. Never write generic, could-be-any-business content.
3. VOICE: Write like a knowledgeable local friend, not a corporate brochure. \
Conversational but professional.
4. STRUCTURE: Hook → Value/Story (2-3 sentences) → CTA or conversation starter. \
Keep captions 40-80 words for Instagram/Facebook, 20-40 words for LinkedIn.
5. VARIETY: Each post should feel different — don't start multiple posts the \
same way or use the same structure twice.
6. NO FLUFF: Cut phrases like "In today's world...", "Are you looking to...", \
"Whether you're a...". Be specific and direct.

HASHTAG RULES:
- 5-8 hashtags per post
- Mix: 2-3 broad industry tags, 2-3 local tags (#AustinTX, #EastAustin), \
1-2 niche/goal-specific tags
- Always include location-specific hashtags
- Never use banned/spammy hashtags (#followforfollow, #like4like)

IMAGE PROMPT RULES:
- Write detailed DALL·E-oriented visual prompts (15-25 words) for downstream image generation.
- Specify: subject, setting, lighting, mood, style
- Match the business type: product shots, lifestyle scenes, team moments, community
- Example: "Modern small business storefront at golden hour, welcoming entrance, \
warm window lighting, urban neighborhood, photorealistic"

VIDEO:
- Always set video_script to empty string "". Short-form video scripts are generated later in the media step.

COMPLIANCE:
- Never reference protected characteristics in a discriminatory way
- Focus on benefits, features and values that appeal to a broad audience
- Avoid absolute claims like "guaranteed results" without appropriate context

SOCIAL PROFILE CONTEXT:
- When the user provides social profile URLs, align captions, hashtags, and image_prompts with the inferred
  brand tone and audience for those channels; keep content relevant to how they likely show up online.
- When no URLs are given, rely on goal, audience, and location alone for a strong generic brand voice.
"""


def _score_content_quality(posts: List[Dict[str, Any]], location: str) -> int:
    """Score generated content 0-100. Used to decide if we should retry.

    Penalties are per-post, so 7 bad posts can easily drop below the
    retry threshold of 50.
    """
    score = 100
    loc_lower = location.lower().replace(",", "").split()
    generic_starts = [
        "in today's", "are you looking", "whether you're",
        "looking to buy", "thinking about", "dreaming of",
        "in the world of", "when it comes to", "have you ever",
    ]
    for p in posts:
        cap = (p.get("caption") or "").lower()
        # Penalize short captions (< 20 words is too thin)
        word_count = len(cap.split())
        if word_count < 10:
            score -= 8
        elif word_count < 20:
            score -= 4
        # Penalize missing local references
        has_local = any(w in cap for w in loc_lower if len(w) > 3)
        if not has_local:
            score -= 5
        # Penalize generic openings
        for g in generic_starts:
            if cap.startswith(g):
                score -= 6
                break
        # Penalize missing/few hashtags
        tags = p.get("hashtags") or []
        if len(tags) < 3:
            score -= 4
    return max(0, score)


def content_node(state: AgentState) -> Dict[str, Any]:
    data = _campaign_data(state)
    num_posts = state.get("num_posts") or _num_posts_for_frequency(data.get("frequency", "3 per week"))
    strat = state.get("strategy_plan") or {}
    day_rows = strat.get("days") or []
    if len(day_rows) < num_posts:
        raise CampaignPipelineError(
            f"Content step requires {num_posts} strategy days; got {len(day_rows)}."
        )
    key = _openai_api_key()
    location = data.get("location", "the local area")
    log.info(
        "[agent:content] ai_text=%s key_present=%s",
        _ai_text_on(state),
        bool(key),
    )

    if not _ai_text_on(state):
        raise CampaignPipelineError(
            "AI content is disabled. Enable AI captions & strategy in the wizard."
        )
    if not key:
        raise OpenAINotConfiguredError(
            "OPENAI_API_KEY is required for caption and hashtag generation."
        )

    platforms = data.get("platforms") or ["facebook"]
    platform_str = ", ".join(platforms)
    audience = data.get("audience") or "potential customers in the local area"
    goal = data.get("goal", "grow brand awareness")
    biz = data.get("business_type", "small business")
    social_block = _social_presence_prompt_block(data)
    ctx = json.dumps({"campaign": data, "strategy_days": day_rows[:num_posts]})

    msg = (
        f"Write exactly {num_posts} social media posts for a {biz} in {location}.\n\n"
        f"GOAL: {goal}\n"
        f"AUDIENCE: {audience}\n"
        f"PLATFORMS: {platform_str}\n\n"
        f"{social_block}\n\n"
        "For each post, provide: day (matching strategy), caption, hashtags (array), "
        'image_prompt (detailed DALL·E-oriented prompt), video_script (always "").\n\n'
        "IMPORTANT: Make every caption feel like it was written by someone who LIVES in "
        f"{location} and knows the market inside out. Reference specific neighborhoods, "
        "streets, local businesses, parks, or market stats when possible.\n\n"
        f"Strategy context:\n{ctx}"
    )

    posts: List[Dict[str, Any]] = []
    max_attempts = 2
    last_err: Optional[Exception] = None
    for attempt in range(max_attempts):
        try:
            llm = _llm(key).with_structured_output(ContentPack)
            pack: ContentPack = llm.invoke(
                [
                    SystemMessage(content=_CONTENT_SYSTEM),
                    HumanMessage(content=msg),
                ]
            )
            candidate = [p.model_dump() for p in pack.posts[:num_posts]]
            if len(candidate) != num_posts:
                raise CampaignPipelineError(
                    f"Content model returned {len(candidate)} posts; need {num_posts}."
                )
            for c in candidate:
                if not str(c.get("caption") or "").strip():
                    raise CampaignPipelineError("Content generation produced an empty caption.")
                if not str(c.get("image_prompt") or "").strip():
                    raise CampaignPipelineError(
                        "Content generation produced an empty image_prompt (required for image generation)."
                    )
            quality = _score_content_quality(candidate, location)
            log.info(
                "[agent:content] attempt=%s quality=%s posts=%s",
                attempt + 1,
                quality,
                len(candidate),
            )
            if quality >= 50:
                posts = candidate
                break
            if attempt == max_attempts - 1:
                raise CampaignPipelineError(
                    f"Generated captions did not meet quality bar after retries (score={quality})."
                )
            log.info("[agent:content] quality too low (%s), retrying", quality)
        except CampaignPipelineError:
            raise
        except Exception as e:
            last_err = e
            log.exception("[agent:content] structured output attempt %s failed", attempt + 1)
            if attempt == max_attempts - 1:
                raise CampaignPipelineError(
                    f"OpenAI content generation failed: {last_err}"
                ) from last_err

    if len(posts) != num_posts:
        raise CampaignPipelineError(
            f"Content step must produce {num_posts} posts; got {len(posts)}."
        )
    for p in posts:
        p["video_script"] = ""

    return {"posts": posts, "step_log": ["content: posts ready"]}


def media_node(state: AgentState) -> Dict[str, Any]:
    posts = list(state.get("posts") or [])
    data = _campaign_data(state)
    num_posts = state.get("num_posts") or _num_posts_for_frequency(data.get("frequency", "3 per week"))
    strat = state.get("strategy_plan") or {}
    day_rows = strat.get("days") or []

    log.info(
        "[agent:media] posts=%s ai_images=%s video_scripts=%s",
        len(posts),
        _ai_images_on(state),
        _video_scripts_on(state),
    )

    if len(posts) != num_posts:
        raise CampaignPipelineError(
            f"Media step expected {num_posts} posts; got {len(posts)}."
        )
    if not _ai_images_on(state):
        raise CampaignPipelineError(
            "AI images are required. Enable AI images in the campaign wizard."
        )
    if not _openai_api_key():
        raise OpenAINotConfiguredError(
            "OPENAI_API_KEY is required for DALL·E image generation."
        )

    theme_for_day: Dict[str, str] = {}
    for d in day_rows:
        if isinstance(d, dict) and d.get("day"):
            theme_for_day[str(d["day"])] = f"{d.get('theme', '')} — {d.get('angle', '')}"

    out: List[Dict[str, Any]] = []
    for i, p in enumerate(posts):
        cap = str(p.get("caption") or "").strip()
        if not cap:
            raise CampaignPipelineError(
                f"Post index {i}: empty caption before media generation."
            )
        day_key = str(p.get("day") or DAYS[i % len(DAYS)])
        theme = theme_for_day.get(day_key, "")
        full_prompt = build_image_prompt(
            cap,
            campaign_theme=theme,
            content_image_prompt=str(p.get("image_prompt") or ""),
            location=str(data.get("location") or ""),
            goal=str(data.get("goal") or ""),
        )
        url = generate_image(full_prompt)
        u = str(url).strip()
        if not u.lower().startswith("https://"):
            raise CampaignPipelineError(
                f"Post index {i}: image generation returned a non-https URL."
            )

        np = dict(p)
        np["image_url"] = u
        if _video_scripts_on(state):
            script = generate_video_script(
                topic=cap[:800],
                audience=str(data.get("audience") or "local buyers and sellers"),
                location=str(data.get("location") or ""),
                goal=str(data.get("goal") or ""),
            )
            np["video_script"] = video_script_to_storage_value(script)
        else:
            np["video_script"] = ""
        out.append(np)
        log.info("[agent:media] post %s OpenAI image + script ok", i)

    return {
        "posts": out,
        "step_log": ["media: OpenAI images and video scripts applied"],
    }


def _compliance_one(caption: str, *, use_llm: bool) -> ComplianceLLM:
    key = _openai_api_key()
    if use_llm:
        if not key:
            raise OpenAINotConfiguredError(
                "OPENAI_API_KEY is required for AI compliance review."
            )
        try:
            llm = _llm(key).with_structured_output(ComplianceLLM)
            msg = (
                "Review this social media caption for compliance issues: discriminatory language, "
                "misleading claims, unsafe promises, or missing disclaimers. "
                "Return JSON fields passed, issues[], fixed_caption. "
                "If minor issues, set passed true and still list suggestions. "
                "If serious risk, passed false and fixed_caption must be a compliant rewrite.\n\n"
                f"Caption:\n{caption}"
            )
            return llm.invoke(
                [
                    SystemMessage(content="You are a compliance reviewer."),
                    HumanMessage(content=msg),
                ]
            )
        except CampaignPipelineError:
            raise
        except Exception as e:
            log.exception("compliance LLM failed")
            raise CampaignPipelineError(f"Compliance AI review failed: {e}") from e
    raise CampaignPipelineError("AI compliance review is required for this pipeline.")


def compliance_node(state: AgentState) -> Dict[str, Any]:
    posts = list(state.get("posts") or [])
    use_llm = _ai_text_on(state) and bool(_openai_api_key())
    log.info("[agent:compliance] posts=%s openai_review=%s", len(posts), use_llm)
    out = []
    for p in posts:
        cap = p.get("caption") or ""
        r = _compliance_one(cap, use_llm=use_llm)
        if use_llm and not r.passed and r.fixed_caption:
            cap2 = r.fixed_caption
            r2 = _compliance_one(cap2, use_llm=use_llm)
            r = r2
            cap = cap2
        np = dict(p)
        np["caption"] = cap
        np["compliance_passed"] = r.passed
        np["compliance_issues"] = list(r.issues)
        out.append(np)
    return {"posts": out, "step_log": ["compliance: checked"]}


def scheduling_node(state: AgentState) -> Dict[str, Any]:
    data = _campaign_data(state)
    posts = list(state.get("posts") or [])
    start = _parse_start(data.get("start_date"))
    offsets = _schedule_offsets(data.get("frequency", "3 per week"))

    # Timezone-aware scheduling: user's local wall time → naive UTC in DB
    from zoneinfo import ZoneInfo

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    user_tz_name = data.get("timezone") or "UTC"
    post_hour = int(data.get("post_hour", 10))  # Default 10 AM local
    post_hour = max(0, min(23, post_hour))

    try:
        user_tz = ZoneInfo(user_tz_name)
    except Exception:
        log.warning("[agent:scheduling] invalid timezone %s, using UTC", user_tz_name)
        user_tz = ZoneInfo("UTC")
        user_tz_name = "UTC"

    utc_tz = ZoneInfo("UTC")
    time_part = datetime.min.time().replace(hour=post_hour, minute=0)
    log.info("[agent:scheduling] start=%s tz=%s hour=%s", start, user_tz_name, post_hour)
    out = []
    for i, p in enumerate(posts):
        off = offsets[i] if i < len(offsets) else i * 2
        day_cursor = start + timedelta(days=off)
        utc_dt = None
        for _ in range(370):
            local_dt = datetime.combine(day_cursor, time_part)
            local_aware = local_dt.replace(tzinfo=user_tz)
            cand = local_aware.astimezone(utc_tz).replace(tzinfo=None)
            if cand > now_naive:
                utc_dt = cand
                break
            day_cursor += timedelta(days=1)
        if utc_dt is None:
            utc_dt = now_naive + timedelta(minutes=1)
        np = dict(p)
        np["scheduled_at"] = utc_dt
        if not np.get("day"):
            np["day"] = DAYS[i % 7]
        out.append(np)
    return {"posts": out, "step_log": [f"scheduling: timestamps set (tz={user_tz_name})"]}


def persist_posts_node(state: AgentState) -> Dict[str, Any]:
    cid = state["campaign_id"]
    uid = state["user_id"]
    posts = state.get("posts") or []
    data = _campaign_data(state)
    num_posts = state.get("num_posts") or _num_posts_for_frequency(data.get("frequency", "3 per week"))
    raw_plats = data.get("platforms") or ["facebook"]
    if not isinstance(raw_plats, list):
        raw_plats = ["facebook"]
    plats = normalize_platforms([str(x) for x in raw_plats if str(x).strip()])
    if not plats:
        plats = ["facebook"]
    log.info("[agent:persist] campaign_id=%s rows=%s platforms=%s", cid, len(posts), plats)
    if len(posts) != num_posts:
        raise CampaignPipelineError(
            f"Persist expected {num_posts} posts; got {len(posts)}."
        )
    with Session(engine) as session:
        camp = session.get(Campaign, cid)
        if camp:
            camp.status = "pending_approval"
            camp.updated_at = datetime.utcnow()
            session.add(camp)
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        for i, p in enumerate(posts):
            sched = p.get("scheduled_at")
            if isinstance(sched, str):
                try:
                    sched = datetime.fromisoformat(sched.replace("Z", "+00:00"))
                except Exception:
                    sched = None
            if isinstance(sched, datetime) and sched.tzinfo is not None:
                sched = sched.astimezone(timezone.utc).replace(tzinfo=None)
            if sched is None:
                raise CampaignPipelineError(
                    f"Cannot persist post {i + 1}: missing scheduled_at after scheduling."
                )
            if sched <= now_utc:
                raise CampaignPipelineError(
                    f"Cannot persist post {i + 1}: scheduled_at must be in the future (UTC)."
                )
            cap = str(p.get("caption") or "").strip()
            if not cap:
                raise CampaignPipelineError(
                    f"Cannot persist post {i + 1}: caption is empty."
                )
            img = str(p.get("image_url") or "").strip()
            if not img or not img.lower().startswith("https://"):
                raise CampaignPipelineError(
                    f"Cannot persist post {i + 1}: image_url must be a non-empty https URL."
                )
            primary_plat = plats[0] if plats else "facebook"
            row = Post(
                user_id=uid,
                campaign_id=cid,
                caption=cap,
                content=cap,
                platform=primary_plat,
                hashtags=json.dumps([str(x) for x in (p.get("hashtags") or [])]),
                image_url=img,
                video_script=str(p.get("video_script") or ""),
                day_label=str(p.get("day") or ""),
                publish_platforms=list(plats),
                status=POST_REVIEW,
                scheduled_at=sched,
                compliance_passed=p.get("compliance_passed"),
                compliance_checked_at=datetime.utcnow(),
                compliance_issues=json.dumps(p.get("compliance_issues") or []),
                platform_response="{}",
                publish_attempts=0,
                max_attempts=3,
            )
            session.add(row)
        session.commit()
    return {"step_log": ["persist: DB saved"]}


def approval_gate_node(state: AgentState) -> Dict[str, Any]:
    log.info(
        "[agent:approval_gate] campaign_id=%s approved=%s",
        state.get("campaign_id"),
        state.get("approved"),
    )
    return {"step_log": ["approval_gate: reached"]}


def publishing_node(state: AgentState) -> Dict[str, Any]:
    cid = state.get("campaign_id")
    if not state.get("approved"):
        log.warning("[agent:publishing] skipped (not approved) campaign_id=%s", cid)
        return {"step_log": ["publishing: skipped (awaiting approval)"]}
    log.info("[agent:publishing] activating campaign_id=%s", cid)
    with Session(engine) as session:
        camp = session.get(Campaign, cid)
        if not camp:
            return {"step_log": ["publishing: campaign missing"]}
        camp.status = "publishing"
        camp.updated_at = datetime.utcnow()
        session.add(camp)
        immediate = os.getenv("BROKERAI_PUBLISH_IMMEDIATELY", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stmt = select(Post).where(Post.campaign_id == cid)
        for row in session.exec(stmt).all():
            if row.status == POST_REVIEW:
                try:
                    transition_post_status(
                        session,
                        row,
                        POST_APPROVED,
                        actor="publishing_node",
                        reason="campaign_approved",
                    )
                except ValueError:
                    row.status = POST_APPROVED
            if immediate:
                row.scheduled_at = now
            elif row.scheduled_at is not None:
                s = row.scheduled_at
                if isinstance(s, datetime) and s.tzinfo is not None:
                    s = s.astimezone(timezone.utc).replace(tzinfo=None)
                    row.scheduled_at = s
                if s < now:
                    row.scheduled_at = now
            session.add(row)
        camp.status = "completed"
        camp.updated_at = datetime.utcnow()
        session.add(camp)
        session.commit()
        sample = session.exec(
            select(Post).where(Post.campaign_id == cid).limit(1)
        ).first()
        if sample:
            log.info(
                "[agent:publishing] ayrshare_platforms=%s (from persisted post)",
                coerce_ayrshare_platforms(sample.publish_platforms),
            )
    return {"step_log": ["publishing: posts approved for Ayrshare scheduler"]}


def lead_capture_node(state: AgentState) -> Dict[str, Any]:
    """9th node — auto-creates a LeadForm and/or CommentAutomation in the DB
    using the configuration collected in wizard Step 5.

    This runs after publishing_node so the campaign_id is fully persisted.
    Both configs are optional: if the wizard user didn't enable them the node
    is a no-op.
    """
    from backend.models import CommentAutomation, LeadForm  # local import avoids circular

    cid = state.get("campaign_id")
    uid = state.get("user_id")
    lf_cfg: Dict[str, Any] = state.get("lead_form_config") or {}
    auto_cfg: Dict[str, Any] = state.get("automation_config") or {}

    step_messages: List[str] = []

    if not (lf_cfg or auto_cfg):
        return {"step_log": ["lead_capture: skipped (no config)"]}

    lead_form_id: Optional[int] = None

    with Session(engine) as session:
        # ── 1. Create lead form ────────────────────────────────────────────────
        if lf_cfg.get("enabled"):
            import secrets as _secrets

            fields_default = [
                {"key": "name",  "label": "Full Name",    "type": "text",  "required": True},
                {"key": "email", "label": "Email",         "type": "email", "required": True},
                {"key": "phone", "label": "Phone Number",  "type": "tel",   "required": False},
            ]
            custom_fields = lf_cfg.get("fields") or fields_default

            lf = LeadForm(
                user_id=uid,
                name=lf_cfg.get("form_name") or f"Campaign {cid} Lead Form",
                headline=lf_cfg.get("headline") or "Get More Info",
                description=lf_cfg.get("description") or "",
                fields=json.dumps(custom_fields),
                thank_you_message=lf_cfg.get("thank_you_message")
                    or "Thanks! We'll be in touch soon.",
                redirect_url=lf_cfg.get("redirect_url") or "",
                public_slug=f"c{cid}-{_secrets.token_urlsafe(6)}",
                is_active=True,
            )
            session.add(lf)
            session.flush()          # get lf.id before commit
            lead_form_id = lf.id
            step_messages.append(f"lead_capture: created lead_form id={lead_form_id}")
            log.info("[agent:lead_capture] created LeadForm id=%s campaign_id=%s", lead_form_id, cid)

        # ── 2. Create comment automation ───────────────────────────────────────
        if auto_cfg.get("enabled"):
            keyword = auto_cfg.get("trigger_keyword") or "info"
            keywords_list = [k.strip().lower() for k in keyword.split(",") if k.strip()]

            platforms: List[str] = auto_cfg.get("platforms") or ["instagram"]
            reply_dm: str = auto_cfg.get("reply_dm") or (
                "Hi {handle}! Here's the info you requested: {link}"
            )
            public_comment_reply: str = auto_cfg.get("public_reply") or (
                "Thanks for the interest! Just sent you a DM 📩"
            )

            # Create one automation per platform
            for plat in platforms:
                auto = CommentAutomation(
                    user_id=uid,
                    name=f"Campaign {cid} — {plat.title()} automation",
                    platform=plat,
                    keywords=json.dumps(keywords_list),
                    match_mode=auto_cfg.get("match_mode") or "any",
                    reply_comment_enabled=True,
                    reply_comment_template=public_comment_reply,
                    dm_enabled=True,
                    dm_template=reply_dm,
                    lead_form_id=lead_form_id,
                    link_url=auto_cfg.get("link_url") or "",
                    is_active=True,
                )
                session.add(auto)
                step_messages.append(
                    f"lead_capture: created comment_automation platform={plat}"
                )
                log.info(
                    "[agent:lead_capture] created CommentAutomation platform=%s campaign_id=%s",
                    plat, cid,
                )

        session.commit()

    return {"step_log": step_messages or ["lead_capture: completed"]}
