"""OpenAI-powered performance analysis across posts with metrics."""
from __future__ import annotations

import json
from typing import Any, Dict, List

import backend.env_loader  # noqa: F401

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from backend.agents.nodes import _openai_api_key
from backend.core.logger import get_logger

log = get_logger("brokerai.ai_analytics")

_SYSTEM = """You are a social media growth expert.

Analyze the following posts with performance metrics:
- Identify patterns in high-performing posts
- Identify why low-performing posts may have underperformed (avoid harsh blame; focus on learnable factors)
- Suggest 5 actionable improvements
- Suggest next 3 post ideas based on best performers

Fair housing: never suggest targeting or excluding protected classes.

Return structured fields only (no markdown)."""


class PerformanceAIResult(BaseModel):
    insights: List[str] = Field(default_factory=list)
    mistakes: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    next_post_ideas: List[str] = Field(default_factory=list)


def _fallback(posts: List[Dict[str, Any]]) -> PerformanceAIResult:
    if not posts:
        return PerformanceAIResult(
            insights=["No posts to analyze yet."],
            mistakes=[],
            recommendations=["Create and publish a few posts, then refresh analytics."],
            next_post_ideas=[
                "Local market snapshot with one chart or stat",
                "Client success story (with permission)",
                "Neighborhood spotlight + one practical tip",
            ],
        )
    rates = [float(p.get("engagement_rate") or 0) for p in posts]
    avg = sum(rates) / len(rates) if rates else 0.0
    top = max(posts, key=lambda p: float(p.get("engagement_rate") or 0))
    return PerformanceAIResult(
        insights=[
            f"Analyzed {len(posts)} posts; average engagement rate ≈ {avg:.2f}%.",
            f"Strongest post in this set (by engagement rate) is id={top.get('id')} on {top.get('platform') or 'unknown'}.",
        ],
        mistakes=[
            "Without more live data, treat very low engagement as a signal to test hooks, visuals, and posting times—not a final verdict.",
        ],
        recommendations=[
            "Lead with a clear hook in the first line; keep captions scannable with short paragraphs.",
            "Use 5–8 hashtags mixing local, niche, and broad tags.",
            "Alternate educational posts with proof/social posts (reviews, milestones).",
            "Post when your audience is active; reuse top themes weekly.",
            "Add a single CTA per post (save, comment, DM, or link).",
        ],
        next_post_ideas=[
            "Repurpose the angle of your best post with a new headline and image.",
            "Myth vs fact about buying/selling in your market.",
            "Checklist: 3 things to do before listing (or before offers).",
        ],
    )


def analyze_performance(posts: List[Dict[str, Any]]) -> PerformanceAIResult:
    """
    posts: lightweight dicts (id, platform, content, likes, comments, impressions, engagement_rate, status).
    """
    if not posts:
        return _fallback([])

    key = _openai_api_key()
    if not key:
        log.warning("[ai_analytics] OPENAI_API_KEY missing — template analysis")
        return _fallback(posts)

    payload = json.dumps(posts, default=str, indent=2)[:14000]
    human = (
        "Analyze the following posts and return JSON-shaped structured output.\n\n"
        f"POSTS_JSON:\n{payload}"
    )

    try:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.35, api_key=key).with_structured_output(
            PerformanceAIResult
        )
        out: PerformanceAIResult = llm.invoke(
            [
                SystemMessage(content=_SYSTEM),
                HumanMessage(content=human),
            ]
        )
        for field in ("insights", "mistakes", "recommendations", "next_post_ideas"):
            if not getattr(out, field):
                setattr(out, field, [])
        return out
    except Exception:
        log.exception("[ai_analytics] LLM call failed — fallback")
        return _fallback(posts)
