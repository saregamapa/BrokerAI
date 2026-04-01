"""
Real OpenAI media: DALL·E (or configured image model) and structured short-form video scripts.

Used by the LangGraph media node. Fails loudly on misconfiguration or API errors — no placeholders.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import OpenAI
from pydantic import BaseModel, Field

from backend.agents.errors import CampaignPipelineError, OpenAINotConfiguredError
from backend.core.logger import get_logger

log = get_logger("brokerai.ai_media")

_BAD_KEYS = frozenset(
    {"your_key_here", "sk-your-key-here", "sk-proj-replace-me", "replace_me"}
)


def _api_key() -> str:
    k = os.getenv("OPENAI_API_KEY", "").strip()
    if not k or k in _BAD_KEYS or k.lower().replace(" ", "") in _BAD_KEYS:
        raise OpenAINotConfiguredError(
            "OPENAI_API_KEY is not set or is a placeholder. Configure a valid API key."
        )
    return k


def _chat() -> ChatOpenAI:
    return ChatOpenAI(model="gpt-4o-mini", temperature=0.7, api_key=_api_key())


class VideoScriptSchema(BaseModel):
    """30-second Reels/Shorts style script."""

    hook: str = Field(description="First 3–5 seconds, pattern interrupt or question")
    scenes: List[str] = Field(
        default_factory=list,
        description="3–6 short scene beats (visual + action), ~5s each",
    )
    voiceover: str = Field(description="Full spoken script, ~30 seconds read aloud")
    cta: str = Field(description="Closing call-to-action")


def build_image_prompt(
    caption: str,
    *,
    campaign_theme: str = "",
    content_image_prompt: str = "",
    location: str = "",
    goal: str = "",
) -> str:
    """
    Compose a DALL·E-ready prompt: professional brand/business marketing visual.
    """
    cap = (caption or "").strip()[:500]
    parts: List[str] = [
        "Professional business social media marketing photograph, clean modern aesthetic, "
        "natural lighting, high detail, photorealistic, no text overlays, no watermarks, "
        "no logos, suitable for Instagram and Facebook.",
        f"Concept aligned with this post: {cap}",
    ]
    if (content_image_prompt or "").strip():
        parts.append(f"Visual direction: {content_image_prompt.strip()[:400]}")
    if (campaign_theme or "").strip():
        parts.append(f"Week theme context: {campaign_theme.strip()[:200]}")
    if (location or "").strip():
        parts.append(f"Setting should evoke this locale (architecture/vibe only, no discriminatory cues): {location.strip()[:120]}")
    if (goal or "").strip():
        parts.append(f"Tone should support campaign goal: {goal.strip()[:120]}")
    parts.append(
        "Show an inviting, professional business context with "
        "diverse adults in a welcoming setting if people appear; inclusive and non-discriminatory."
    )
    return " ".join(parts)[:4000]


def generate_image(prompt: str) -> str:
    """
    Generate a marketing image via OpenAI Images API. Returns an HTTPS URL to the image.
    """
    key = _api_key()
    p = (prompt or "").strip()
    if not p:
        raise CampaignPipelineError("Image generation failed: empty prompt.")

    model = (os.getenv("OPENAI_IMAGE_MODEL") or "dall-e-3").strip()
    client = OpenAI(api_key=key)

    params: Dict[str, Any] = {"model": model, "prompt": p[:4000], "n": 1}
    if model == "dall-e-3":
        params["size"] = "1024x1024"
        params["quality"] = "standard"
    elif model == "dall-e-2":
        params["size"] = "1024x1024"
    else:
        # e.g. gpt-image-1 — size often required
        params["size"] = os.getenv("OPENAI_IMAGE_SIZE", "1024x1024")

    try:
        response = client.images.generate(**params)
    except Exception as e:
        log.exception("OpenAI images.generate failed model=%s", model)
        raise CampaignPipelineError(f"OpenAI image generation failed: {e}") from e

    if not response.data:
        raise CampaignPipelineError("OpenAI returned no image data.")

    item = response.data[0]
    url = getattr(item, "url", None)
    if url and str(url).strip().lower().startswith("https://"):
        return str(url).strip()

    b64 = getattr(item, "b64_json", None)
    if b64:
        raise CampaignPipelineError(
            "OpenAI returned a base64 image only; BrokerAI requires a hosted image URL for publishing. "
            "Use an image model that returns urls (e.g. dall-e-3) or extend storage to upload b64."
        )

    raise CampaignPipelineError("OpenAI image response had no usable URL.")


def generate_video_script(
    topic: str,
    audience: str,
    *,
    location: str = "",
    goal: str = "",
) -> Dict[str, Any]:
    """
    Structured short-form video script (hook, scenes, voiceover, cta) via OpenAI chat + JSON schema.
    """
    topic = (topic or "").strip()
    aud = (audience or "").strip()
    if not topic:
        raise CampaignPipelineError("Video script generation failed: empty topic.")

    llm = _chat().with_structured_output(VideoScriptSchema)
    loc = (location or "").strip()
    gl = (goal or "").strip()
    human = (
        f"Create a 30-second social media video script for Reels/Shorts.\n\n"
        f"Topic / post focus: {topic}\n"
        f"Audience: {aud}\n"
        + (f"Location/market: {loc}\n" if loc else "")
        + (f"Campaign goal: {gl}\n" if gl else "")
        + "\n"
        "Scenes should be concrete and filmable. Voiceover should match the hook and scenes. "
        "Keep content inclusive and avoid targeting or excluding protected characteristics."
    )
    try:
        out: VideoScriptSchema = llm.invoke(
            [
                SystemMessage(
                    content="You write tight, high-retention short-form social media video scripts for businesses and brands."
                ),
                HumanMessage(content=human),
            ]
        )
    except Exception as e:
        log.exception("Video script structured generation failed")
        raise CampaignPipelineError(f"OpenAI video script generation failed: {e}") from e

    return out.model_dump()


def video_script_to_storage_value(data: Dict[str, Any]) -> str:
    """Serialize script dict for Post.video_script (TEXT/JSON string)."""
    return json.dumps(data, ensure_ascii=False)
