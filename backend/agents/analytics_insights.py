"""Analytics Agent — LLM insights and recommendations from post performance + copy."""
from __future__ import annotations

import json
from typing import Any, Dict, List

import backend.env_loader  # noqa: F401

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from backend.agents.nodes import _openai_api_key
from backend.core.logger import get_logger

log = get_logger("brokerai.agents.analytics")

_ANALYTICS_SYSTEM = """You are the BrokerAI Analytics Agent for real estate social campaigns.

You receive structured data for each post: performance metrics (likes, comments, shares, impressions, engagement_rate as a percentage), caption text, hashtags, platforms, publish status, and scheduled/published hour (UTC).

Your job:
1. **insights** — Short, specific observations about what the data suggests (themes, length, timing patterns, relative winners, weak spots). If metrics look like placeholders or all zeros, say that clearly and infer only cautiously from captions/hashtags.
2. **recommendations** — Concrete ideas for the *next* campaign (content pillars, audience, cadence, caption style, hashtag strategy). Must be actionable, not generic fluff.

Rules:
- Output valid structured fields only (no markdown).
- 3–8 items per list; each string one sentence when possible.
- Fair Housing: never suggest targeting or excluding protected classes.
- Do not invent metrics not supported by the input.
"""


class CampaignInsightsResult(BaseModel):
    insights: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)


def _fallback_insights(summaries: List[Dict[str, Any]]) -> CampaignInsightsResult:
    published = [s for s in summaries if s.get("status") == "published"]
    rates = [float(s.get("engagement_rate_pct") or 0) for s in summaries]
    avg = sum(rates) / len(rates) if rates else 0.0
    insights: List[str] = []
    recs: List[str] = []

    if not summaries:
        return CampaignInsightsResult(
            insights=["No posts found for this campaign."],
            recommendations=["Add posts to the campaign, then publish to unlock performance-based suggestions."],
        )

    if not published:
        insights.append(
            "None of these posts are published yet — engagement metrics may be empty until content goes live."
        )
        recs.append("Publish approved posts so real performance data can drive the next campaign plan.")
    else:
        insights.append(
            f"{len(published)} of {len(summaries)} posts are published; compare their engagement rates to spot winners."
        )

    if avg > 0:
        insights.append(f"Average engagement rate across posts is about {avg:.1f}%.")

    top = max(summaries, key=lambda s: float(s.get("engagement_rate_pct") or 0))
    if float(top.get("engagement_rate_pct") or 0) > 0:
        insights.append(
            f"Strongest post by engagement (≈{float(top.get('engagement_rate_pct') or 0):.1f}%): "
            f"review its caption angle and hashtags for patterns to repeat."
        )

    cap_lens = [len((s.get("caption_excerpt") or "").split()) for s in summaries]
    if cap_lens:
        insights.append(
            f"Caption lengths range ~{min(cap_lens)}–{max(cap_lens)} words; test whether shorter or longer copy correlates with engagement."
        )

    recs.append("Double down on the themes and hooks used in your highest-engagement posts.")
    recs.append("Run an A/B style next week: alternate educational tips vs. community/story posts.")
    recs.append("Tighten primary hashtags to 5–8 strong tags (mix local + niche + broad).")

    return CampaignInsightsResult(
        insights=insights[:8] or ["Not enough signal yet — keep publishing and refresh insights."],
        recommendations=recs[:8] or ["Continue publishing, then re-run insights."],
    )


def run_campaign_insights(post_summaries: List[Dict[str, Any]]) -> CampaignInsightsResult:
    """
    Synchronous LLM call — run via asyncio.to_thread from FastAPI.
    """
    if not post_summaries:
        return _fallback_insights([])

    key = _openai_api_key()
    if not key:
        log.warning("[analytics_agent] OPENAI_API_KEY missing — template insights")
        return _fallback_insights(post_summaries)

    payload = json.dumps(post_summaries, default=str, indent=2)[:12000]
    human = (
        "Analyze this campaign's posts and return insights + recommendations.\n\n"
        f"POST_DATA_JSON:\n{payload}"
    )

    try:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.35, api_key=key).with_structured_output(
            CampaignInsightsResult
        )
        out: CampaignInsightsResult = llm.invoke(
            [
                SystemMessage(content=_ANALYTICS_SYSTEM),
                HumanMessage(content=human),
            ]
        )
        if not out.insights:
            out.insights = ["Model returned no insights — see recommendations."]
        if not out.recommendations:
            out.recommendations = ["Refresh data after more posts are published."]
        return CampaignInsightsResult(
            insights=out.insights[:8],
            recommendations=out.recommendations[:8],
        )
    except Exception:
        log.exception("[analytics_agent] LLM structured output failed — fallback")
        return _fallback_insights(post_summaries)
