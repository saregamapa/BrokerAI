"""CaptionAgent — wizard Step 3 preview + LangGraph ``content`` node.

Product mapping
-----------------
- **Wizard Step 3 (AI captions)** calls :func:`preview_caption_prompts` to build
  OpenAI messages. The HTTP handler parses JSON into structured variants.
- **LangGraph** still registers this agent under the node key ``content`` (see
  ``graph.py``) so existing checkpoints remain valid; semantically it is the
  CaptionAgent / post-copy step.

Outputs for preview match the wizard contract::

    { "caption": str, "hashtags": [str, ...] }

Full pipeline copy lives in ``backend.agents.nodes.content_node`` (structured
``ContentPack`` with per-post captions, hashtag arrays, and image prompts).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from pydantic import BaseModel, Field

from backend.schemas import CaptionVariantOut, PreviewCaptionsRequest


class _PreviewLLMShape(BaseModel):
    """Strict envelope for OpenAI JSON-mode preview."""

    variants: List[Dict[str, Any]] = Field(default_factory=list)


def _normalize_hashtag(h: str) -> str:
    s = str(h or "").strip()
    if not s:
        return ""
    return s[1:].strip() if s.startswith("#") else s.strip()


def preview_caption_system(body: PreviewCaptionsRequest) -> str:
    platform_hint = ", ".join(body.platforms) if body.platforms else "Instagram, Facebook, LinkedIn"
    return (
        "You are CaptionAgent, an expert social copywriter for local businesses and real estate. "
        "Return ONLY valid JSON matching this shape exactly:\n"
        '{"variants":[{"caption":"...","hashtags":["TagOne","TagTwo"]}, ...]}\n'
        "Rules:\n"
        "- `caption` is the main post body only (no hashtag line inside caption).\n"
        "- `hashtags` is 3–8 topical tags WITHOUT the # prefix in the JSON strings.\n"
        "- Vary hooks and angles across variants; keep each caption platform-native.\n"
        f"- Tone: {body.tone}. Platforms to optimize for: {platform_hint}.\n"
        "- Include 1–3 tasteful emojis in some captions where it fits the brand voice.\n"
        "No markdown, no commentary."
    )


def preview_caption_user_payload(body: PreviewCaptionsRequest) -> str:
    tpl = body.wizard_template if isinstance(body.wizard_template, dict) else {}
    payload: Dict[str, Any] = {
        "industry_bucket": body.bucket.replace("_", " "),
        "persona": body.persona,
        "goal": body.goal,
        "goal_category": body.campaign_goal_category or "",
        "location": body.location,
        "platforms": body.platforms,
        "variant_count": body.count,
        "selected_template_name": body.selected_template or "",
        "template_visual_hint": (tpl.get("name") or "")[:120] if tpl else "",
        "preferred_hook_energy": body.selected_caption_hook or "",
        "has_media_attachment": bool(body.has_media_attachment),
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_preview_variants(data: Dict[str, Any], *, count: int) -> Tuple[List[CaptionVariantOut], List[str]]:
    """Parse LLM JSON into structured variants + legacy caption strings."""
    raw_vars: List[Any] = []
    try:
        shaped = _PreviewLLMShape.model_validate(data)
        raw_vars = list(shaped.variants or [])
    except Exception:
        raw = data.get("variants")
        raw_vars = list(raw) if isinstance(raw, list) else []

    out: List[CaptionVariantOut] = []
    for item in raw_vars[:count]:
        if not isinstance(item, dict):
            continue
        cap = str(item.get("caption") or "").strip()
        tags_raw = item.get("hashtags") or item.get("tags") or []
        tags: List[str] = []
        if isinstance(tags_raw, list):
            for t in tags_raw:
                n = _normalize_hashtag(str(t))
                if n:
                    tags.append(n)
        elif isinstance(tags_raw, str):
            for part in re.split(r"[\s,]+", tags_raw):
                n = _normalize_hashtag(part)
                if n:
                    tags.append(n)
        if cap:
            out.append(CaptionVariantOut(caption=cap, hashtags=tags[:12]))

    legacy: List[str] = []
    for v in out:
        tag_line = " ".join(f"#{t}" for t in v.hashtags) if v.hashtags else ""
        legacy.append(f"{v.caption}\n\n{tag_line}".strip() if tag_line else v.caption)

    return out, legacy
