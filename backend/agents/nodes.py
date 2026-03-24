import json
import os
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import backend.env_loader  # noqa: F401 — ensure project root .env loaded if agents imported first

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from backend.agents.state import AgentState
from backend.core.logger import get_logger
from backend.db import engine
from backend.integrations.ayrshare import coerce_ayrshare_platforms, normalize_platforms
from backend.models import Campaign, Post

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

_BAD_PLACEHOLDER_KEYS = frozenset(
    {"your_key_here", "sk-your-key-here", "sk-proj-replace-me", "replace_me"}
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
            "Use a professional, warm, trustworthy real estate tone that works across "
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
    f = (freq or "").lower()
    if "daily" in f or "every day" in f:
        return list(range(7))
    if "5" in f and "week" in f:
        return [0, 1, 2, 3, 4, 7, 8]
    if "2" in f and "week" in f:
        return [0, 3, 7, 10, 14, 17, 21]
    return [0, 2, 4, 7, 9, 11, 14]


def _placeholder_image(i: int) -> str:
    return f"https://placehold.co/800x450/1e293b/94a3b8?text=Post+{i + 1}"


def _fallback_strategy_days(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "day": d,
            "theme": f"{data.get('goal', 'engagement')} — {data.get('location')}",
            "angle": "value + trust",
        }
        for d in DAYS
    ]


def _fallback_posts(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    posts = []
    for i, d in enumerate(DAYS):
        posts.append(
            {
                "day": d,
                "caption": (
                    f"{data.get('goal', 'Real estate')} in {data.get('location')}? "
                    f"I'm here to help — no pressure, local expertise."
                ),
                "hashtags": [
                    "#realestate",
                    f"#{str(data.get('location', '')).replace(' ', '')}",
                    "#home",
                ],
                "image_prompt": f"Bright listing exterior in {data.get('location')}",
                "video_script": (
                    f"Hi, quick tip for {data.get('location')} — reach out to learn more."
                ),
            }
        )
    return posts


def _strip_json_fence(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", t)
    if m:
        return m.group(1).strip()
    return t


def _content_from_json_llm(
    key: str, data: dict, day_rows: list, want_video: bool
) -> Optional[List[Dict[str, Any]]]:
    """Fallback when structured output fails: raw JSON from model."""
    try:
        llm = _llm(key).bind(response_format={"type": "json_object"})
    except (TypeError, AttributeError):
        llm = _llm(key)
    vnote = (
        'Use empty string "" for video_script on every post.'
        if not want_video
        else "Include a short 30–45s voiceover script per post in video_script."
    )
    location = data.get("location", "the local area")
    social_block = _social_presence_prompt_block(data)
    ctx = json.dumps({"campaign": data, "strategy_days": day_rows[:7]})
    msg = (
        "Write exactly 7 social posts as JSON. Shape: "
        '{"posts":[{"day":"Monday","caption":"...","hashtags":["#a","#b"],"image_prompt":"...","video_script":"..."}, ...]}. '
        f"\n\n{social_block}\n\n"
        f"RULES:\n"
        f"- Every caption must reference {location} specifically\n"
        "- Start each caption with a hook (question, bold stat, or pattern interrupt)\n"
        "- 5-8 hashtags per post, mix of broad + local + niche\n"
        "- Image prompts should be 15-25 words, detailed for DALL·E\n"
        "- Fair Housing compliant, no discrimination\n"
        f"- {vnote}\n\n{ctx}"
    )
    raw = llm.invoke(
        [
            SystemMessage(content="You output only valid JSON, no markdown. "
                          "You are an expert real estate social media copywriter."),
            HumanMessage(content=msg),
        ]
    )
    text = raw.content if hasattr(raw, "content") else str(raw)
    try:
        obj = json.loads(_strip_json_fence(text))
        arr = obj.get("posts") or []
        out = []
        for i, item in enumerate(arr[:7]):
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "day": str(item.get("day") or DAYS[i % 7]),
                    "caption": str(item.get("caption") or ""),
                    "hashtags": item.get("hashtags") if isinstance(item.get("hashtags"), list) else [],
                    "image_prompt": str(item.get("image_prompt") or ""),
                    "video_script": str(item.get("video_script") or ""),
                }
            )
        while len(out) < 7:
            out.append(_fallback_posts(data)[len(out)])
        return out[:7]
    except Exception as e:
        log.warning("[agent:content] JSON parse fallback failed: %s", e)
        return None


_STRATEGY_SYSTEM = """\
You are a senior real estate social media strategist who has managed accounts \
for top-producing brokers and teams. You understand what content drives \
engagement, builds trust, and generates leads on social media.

Key principles you follow:
- Mix content types: educational, social proof, community, behind-the-scenes, calls-to-action
- Never post the same type of content two days in a row
- Local relevance beats generic advice — always tie content to the specific market
- Each day should have a clear PURPOSE (educate, engage, convert, nurture)
- Weekend content is lighter and more personal; weekday content is more professional
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
    if _ai_text_on(state) and key:
        try:
            llm = _llm(key).with_structured_output(StrategyPlan)
            location = data.get("location", "the local area")
            audience = data.get("audience") or "local buyers and sellers"
            goal = data.get("goal", "generate leads")
            biz = data.get("business_type", "real estate agent")
            msg = (
                f"Build a 7-day social media content plan for a {biz} in {location}.\n\n"
                f"PRIMARY GOAL: {goal}\n"
                f"TARGET AUDIENCE: {audience}\n\n"
                "Requirements:\n"
                "- Output exactly 7 days (Monday through Sunday)\n"
                "- Each day needs: theme (2-4 words) and angle (one sentence describing the specific post idea)\n"
                "- Vary content types across the week: market insight, social proof/testimonial, "
                "community spotlight, educational tip, behind-the-scenes, listing highlight, personal/lifestyle\n"
                "- Make angles SPECIFIC to {location} — reference neighborhoods, local landmarks, "
                "market conditions, or seasonal relevance when possible\n"
                "- Weekend posts should feel lighter and more personal\n"
                "- At least one day should include a clear call-to-action\n"
                "- All content must be Fair Housing compliant and inclusive"
            ).format(location=location)
            plan: StrategyPlan = llm.invoke(
                [
                    SystemMessage(content=_STRATEGY_SYSTEM),
                    HumanMessage(content=msg),
                ]
            )
            return {
                "strategy_plan": plan.model_dump(),
                "step_log": [f"strategy: OpenAI plan ({len(plan.days)} days)"],
            }
        except Exception:
            log.exception("[agent:strategy] OpenAI structured output failed — using template plan")
    elif _ai_text_on(state) and not key:
        log.warning(
            "[agent:strategy] ai_text_enabled but OPENAI_API_KEY is missing — using template plan"
        )
    return {
        "strategy_plan": {"days": _fallback_strategy_days(data)},
        "step_log": ["strategy: template plan (no AI or AI failed)"],
    }


_CONTENT_SYSTEM = """\
You are a top-performing real estate social media copywriter. Your captions \
consistently get high engagement because you follow these rules:

CAPTION RULES:
1. HOOK FIRST: Every caption starts with an attention-grabbing first line \
(question, bold statement, surprising stat, or pattern interrupt). The first \
line must make someone stop scrolling.
2. LOCAL FLAVOR: Reference the specific city, neighborhoods, local landmarks, \
or market conditions. Never write generic "real estate" content.
3. VOICE: Write like a knowledgeable local friend, not a corporate brochure. \
Conversational but professional.
4. STRUCTURE: Hook → Value/Story (2-3 sentences) → CTA or conversation starter. \
Keep captions 40-80 words for Instagram/Facebook, 20-40 words for LinkedIn.
5. VARIETY: Each post should feel different — don't start multiple posts the \
same way or use the same structure twice.
6. NO FLUFF: Cut phrases like "In today's market...", "Are you looking to...", \
"Whether you're buying or selling...". Be specific and direct.

HASHTAG RULES:
- 5-8 hashtags per post
- Mix: 2-3 broad (#realestate, #homebuying), 2-3 local (#AustinTX, #EastAustinHomes), \
1-2 niche (#FirstTimeHomeBuyer, #InvestmentProperty)
- Always include location-specific hashtags
- Never use banned/spammy hashtags (#followforfollow, #like4like)

IMAGE PROMPT RULES:
- Write detailed DALL·E prompts (15-25 words)
- Specify: subject, setting, lighting, mood, style
- Real estate focused: exteriors, interiors, neighborhoods, lifestyle scenes
- Example: "Modern craftsman home exterior at golden hour, manicured lawn, warm porch lights, \
suburban neighborhood, photorealistic"

FAIR HOUSING:
- Never reference race, color, religion, sex, disability, familial status, or national origin
- Never suggest a neighborhood is "good for families" or "exclusive"
- Focus on property features and market data, not who lives there

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
        "as a real estate", "in the world of", "when it comes to",
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
    strat = state.get("strategy_plan") or {}
    day_rows = strat.get("days") or _fallback_strategy_days(data)
    key = _openai_api_key()
    want_video = _video_scripts_on(state)
    location = data.get("location", "the local area")
    log.info(
        "[agent:content] ai_text=%s key_present=%s video_scripts=%s",
        _ai_text_on(state),
        bool(key),
        want_video,
    )

    posts: List[Dict[str, Any]] = []

    if _ai_text_on(state) and key:
        vline = (
            "\nVIDEO SCRIPT RULES:\n"
            "- Write a 30-45 second voiceover script for each post\n"
            "- Structure: Hook (5s) → Key point (15-20s) → CTA (5-10s)\n"
            "- Write for spoken delivery — short sentences, conversational tone\n"
            "- Start with a question or bold statement to grab attention\n"
            if want_video
            else 'Set video_script to empty string "" for every post.'
        )
        platforms = data.get("platforms") or ["facebook"]
        platform_str = ", ".join(platforms)
        audience = data.get("audience") or "local buyers and sellers"
        goal = data.get("goal", "generate leads")
        biz = data.get("business_type", "real estate agent")
        social_block = _social_presence_prompt_block(data)
        ctx = json.dumps({"campaign": data, "strategy_days": day_rows[:7]})

        msg = (
            f"Write exactly 7 social media posts for a {biz} in {location}.\n\n"
            f"GOAL: {goal}\n"
            f"AUDIENCE: {audience}\n"
            f"PLATFORMS: {platform_str}\n\n"
            f"{social_block}\n\n"
            "For each post, provide: day (matching strategy), caption, hashtags (array), "
            "image_prompt (detailed DALL·E prompt), video_script.\n\n"
            f"{vline}\n\n"
            "IMPORTANT: Make every caption feel like it was written by someone who LIVES in "
            f"{location} and knows the market inside out. Reference specific neighborhoods, "
            "streets, local businesses, parks, or market stats when possible.\n\n"
            f"Strategy context:\n{ctx}"
        )

        max_attempts = 2
        for attempt in range(max_attempts):
            try:
                llm = _llm(key).with_structured_output(ContentPack)
                pack: ContentPack = llm.invoke(
                    [
                        SystemMessage(content=_CONTENT_SYSTEM),
                        HumanMessage(content=msg),
                    ]
                )
                candidate = [p.model_dump() for p in pack.posts[:7]]
                quality = _score_content_quality(candidate, location)
                log.info(
                    "[agent:content] attempt=%s quality=%s posts=%s",
                    attempt + 1, quality, len(candidate),
                )
                if quality >= 50 or attempt == max_attempts - 1:
                    posts = candidate
                    break
                log.info("[agent:content] quality too low (%s), retrying", quality)
            except Exception:
                log.exception("[agent:content] structured output attempt %s failed", attempt + 1)
                if attempt == max_attempts - 1:
                    posts = _content_from_json_llm(key, data, day_rows, want_video) or []

    if len(posts) < 7:
        fb = _fallback_posts(data)
        for i in range(7):
            if i >= len(posts):
                posts.append(fb[i])
            else:
                for k2 in ("caption", "hashtags", "image_prompt", "video_script", "day"):
                    if k2 == "hashtags" and not posts[i].get("hashtags"):
                        posts[i]["hashtags"] = fb[i]["hashtags"]
                    elif k2 != "hashtags" and not posts[i].get(k2):
                        posts[i][k2] = fb[i].get(k2, "")
        posts = posts[:7]

    if not want_video:
        for p in posts:
            p["video_script"] = ""

    return {"posts": posts, "step_log": ["content: posts ready"]}


def media_node(state: AgentState) -> Dict[str, Any]:
    posts = list(state.get("posts") or [])
    key = _openai_api_key()
    images_on = _ai_images_on(state)
    log.info(
        "[agent:media] posts=%s ai_images=%s key_present=%s",
        len(posts),
        images_on,
        bool(key),
    )
    out = []
    for i, p in enumerate(posts):
        prompt = (p.get("image_prompt") or p.get("caption") or "real estate")[:900]
        url = _placeholder_image(i)
        if images_on and key:
            try:
                from openai import OpenAI

                client = OpenAI(api_key=key)
                r = client.images.generate(
                    model="dall-e-3",
                    prompt=prompt,
                    size="1024x1024",
                    quality="standard",
                    n=1,
                )
                u = r.data[0].url
                if u:
                    url = u
                    log.info("[agent:media] DALL·E image %s ok", i)
            except Exception as e:
                log.warning("[agent:media] DALL·E %s failed, placeholder: %s", i, e)
        elif images_on and not key:
            if i == 0:
                log.warning(
                    "[agent:media] ai_images_enabled but OPENAI_API_KEY missing — placeholders only"
                )
        np = dict(p)
        np["image_url"] = url
        out.append(np)
    return {"posts": out, "step_log": ["media: image URLs set"]}


def _compliance_one(caption: str, *, use_llm: bool) -> ComplianceLLM:
    key = _openai_api_key()
    if use_llm and key:
        try:
            llm = _llm(key).with_structured_output(ComplianceLLM)
            msg = (
                "Review this real estate social caption for US Fair Housing risk, discrimination, "
                "steering, or unsafe claims. Return JSON fields passed, issues[], fixed_caption. "
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
        except Exception as e:
            log.warning("compliance LLM failed: %s", e)
    low = caption.lower()
    issues = []
    for bad, note in [
        ("families only", "familial status"),
        ("no children", "familial status"),
        ("christian only", "religion"),
        ("exclusive neighborhood", "steering"),
    ]:
        if bad in low:
            issues.append(note)
    fixed = caption
    if issues:
        fixed = (caption + "\n\nEqual Housing Opportunity.").strip()
    return ComplianceLLM(passed=len(issues) == 0, issues=issues, fixed_caption=fixed)


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

    # Timezone-aware scheduling: convert user's local time to UTC
    from zoneinfo import ZoneInfo

    user_tz_name = data.get("timezone") or "America/New_York"
    post_hour = int(data.get("post_hour", 10))  # Default 10 AM local
    post_hour = max(0, min(23, post_hour))

    try:
        user_tz = ZoneInfo(user_tz_name)
    except Exception:
        log.warning("[agent:scheduling] invalid timezone %s, using America/New_York", user_tz_name)
        user_tz = ZoneInfo("America/New_York")

    utc_tz = ZoneInfo("UTC")
    log.info("[agent:scheduling] start=%s tz=%s hour=%s", start, user_tz_name, post_hour)
    out = []
    for i, p in enumerate(posts):
        off = offsets[i] if i < len(offsets) else i * 2
        # Build a timezone-aware datetime in the user's local time
        local_dt = datetime.combine(
            start + timedelta(days=off),
            datetime.min.time().replace(hour=post_hour, minute=0),
        )
        local_aware = local_dt.replace(tzinfo=user_tz)
        # Convert to UTC for storage (scheduler compares against utcnow)
        utc_dt = local_aware.astimezone(utc_tz).replace(tzinfo=None)
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
    raw_plats = data.get("platforms") or ["facebook"]
    if not isinstance(raw_plats, list):
        raw_plats = ["facebook"]
    plats = normalize_platforms([str(x) for x in raw_plats if str(x).strip()])
    if not plats:
        plats = ["facebook"]
    log.info("[agent:persist] campaign_id=%s rows=%s platforms=%s", cid, len(posts), plats)
    with Session(engine) as session:
        camp = session.get(Campaign, cid)
        if camp:
            camp.status = "pending_approval"
            camp.updated_at = datetime.utcnow()
            session.add(camp)
        for i, p in enumerate(posts):
            sched = p.get("scheduled_at")
            if isinstance(sched, str):
                try:
                    sched = datetime.fromisoformat(sched.replace("Z", "+00:00"))
                except Exception:
                    sched = datetime.utcnow()
            if sched is None:
                sched = datetime.utcnow()
            row = Post(
                user_id=uid,
                campaign_id=cid,
                caption=str(p.get("caption") or ""),
                hashtags=json.dumps([str(x) for x in (p.get("hashtags") or [])]),
                image_url=str(p.get("image_url") or ""),
                video_script=str(p.get("video_script") or ""),
                day_label=str(p.get("day") or ""),
                publish_platforms=list(plats),
                status="pending_approval",
                scheduled_at=sched,
                compliance_passed=p.get("compliance_passed"),
                compliance_checked_at=datetime.utcnow(),
                compliance_issues=json.dumps(p.get("compliance_issues") or []),
                platform_response="{}",
                publish_attempts=0,
                idempotency_key=f"camp-{cid}-post-{i}-{uuid.uuid4().hex[:8]}",
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
        now = datetime.utcnow()
        stmt = select(Post).where(Post.campaign_id == cid)
        for row in session.exec(stmt).all():
            if row.status == "pending_approval":
                row.status = "approved"
            if immediate:
                row.scheduled_at = now
            elif row.scheduled_at is not None and row.scheduled_at < now:
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
