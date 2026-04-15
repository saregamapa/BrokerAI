"""
LangChain-powered refinement for wizard Step 5 (lead forms + comment-to-DM).

Produces structured copy that is merged with the user's raw wizard config before
persisting LeadForm / CommentAutomation rows.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from backend.core.logger import get_logger

log = get_logger("brokerai.agents.lead_capture")

_BAD_PLACEHOLDER_KEYS = frozenset(
    {"your_key_here", "sk-your-key-here", "sk-proj-replace-me", "replace_me"}
)


def _openai_key() -> str:
    import os

    k = os.getenv("OPENAI_API_KEY", "").strip()
    if not k:
        return ""
    low = k.lower().replace(" ", "")
    if k in _BAD_PLACEHOLDER_KEYS or low in _BAD_PLACEHOLDER_KEYS:
        return ""
    return k


class LeadCapturePlan(BaseModel):
    """Structured output for lead capture + DM automation."""

    form_headline: str = Field(description="Short compelling headline for the lead form")
    form_description: str = Field(
        default="",
        description="1-2 sentence description shown above the form fields",
    )
    thank_you_message: str = Field(
        default="Thanks! We'll be in touch soon.",
        description="Post-submit confirmation message",
    )
    public_comment_reply: str = Field(
        default="Thanks for the interest! Just sent you a DM 📩",
        description="Public reply when a comment matches the trigger",
    )
    dm_template: str = Field(
        default="Hi {handle}! Here's the info you requested: {link}",
        description="DM body; may use {handle}, {link}, {keyword}, {comment}",
    )
    rationale: str = Field(default="", description="Internal one-line rationale (optional)")


_LEAD_SYSTEM = """You are a conversion-focused marketing automation specialist for social media.
Given a campaign context and the user's lead-capture preferences, produce polished copy for:
1) A hosted lead form headline + short description + thank-you line
2) A public comment reply and a DM template for keyword-triggered automations

Rules:
- Keep headlines under 90 characters; descriptions under 280 characters.
- DM templates MUST keep placeholders exactly as provided when the user already uses them:
  {handle}, {link}, {keyword}, {comment} — preserve any the user relied on.
- If the user did not enable a lead form, still return sensible defaults (they will be ignored upstream).
- Tone: professional, trustworthy, inclusive; no discriminatory or absolute medical/financial claims.
"""


def run_lead_capture_agent(
    *,
    campaign_goal: str,
    business_type: str,
    audience: str,
    lead_form_config: Optional[Dict[str, Any]],
    automation_config: Optional[Dict[str, Any]],
) -> LeadCapturePlan:
    """Invoke GPT-4o-mini with structured output. Falls back to heuristic defaults if OpenAI fails."""
    key = _openai_key()
    lf = lead_form_config or {}
    auto = automation_config or {}

    if not key:
        return LeadCapturePlan(
            form_headline=str(lf.get("headline") or "Get in touch"),
            form_description=str(lf.get("description") or ""),
            thank_you_message="Thanks! We'll be in touch soon.",
            public_comment_reply=str(
                auto.get("public_reply") or "Thanks for the interest! Just sent you a DM 📩"
            ),
            dm_template=str(
                auto.get("reply_dm") or "Hi {handle}! Here's the info you requested: {link}"
            ),
            rationale="OpenAI not configured; using wizard defaults.",
        )

    human = json.dumps(
        {
            "campaign_goal": campaign_goal,
            "business_type": business_type,
            "audience": audience,
            "lead_form_config": lf,
            "automation_config": auto,
        },
        ensure_ascii=False,
        default=str,
    )

    try:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.45, api_key=key).with_structured_output(
            LeadCapturePlan
        )
        plan: LeadCapturePlan = llm.invoke(
            [
                SystemMessage(content=_LEAD_SYSTEM),
                HumanMessage(
                    content="Produce the structured lead capture plan for this JSON:\n" + human
                ),
            ]
        )
        log.info("[lead_capture_agent] structured plan ok")
        return plan
    except Exception as e:
        log.warning("[lead_capture_agent] LLM failed, using defaults: %s", e)
        return LeadCapturePlan(
            form_headline=str(lf.get("headline") or "Get in touch"),
            form_description=str(lf.get("description") or ""),
            thank_you_message="Thanks! We'll be in touch soon.",
            public_comment_reply=str(
                auto.get("public_reply") or "Thanks for the interest! Just sent you a DM 📩"
            ),
            dm_template=str(
                auto.get("reply_dm") or "Hi {handle}! Here's the info you requested: {link}"
            ),
            rationale="fallback",
        )
