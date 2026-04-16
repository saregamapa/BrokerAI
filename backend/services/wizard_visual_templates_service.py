"""Wizard \"Choose your visuals\" template grid: OpenAI suggests copy + Unsplash queries; Unsplash provides photos."""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from backend.agents.errors import OpenAINotConfiguredError
from backend.core.logger import get_logger
from backend.services.ai_media_service import _api_key

log = get_logger("brokerai.wizard_visual_templates")

_PAD_GRADIENTS = [
    "linear-gradient(135deg,#0f172a 0%,#312e81 50%,#7c3aed 100%)",
    "linear-gradient(135deg,#14532d 0%,#166534 45%,#4ade80 100%)",
    "linear-gradient(135deg,#7c2d12 0%,#ea580c 50%,#fbbf24 100%)",
    "linear-gradient(135deg,#0c4a6e 0%,#0369a1 50%,#38bdf8 100%)",
    "linear-gradient(135deg,#4a044e 0%,#a21caf 55%,#f0abfc 100%)",
    "linear-gradient(135deg,#1e293b 0%,#475569 50%,#94a3b8 100%)",
]

_PAD_UNSPLASH = [
    "minimal architecture building",
    "luxury living room interior",
    "city skyline dusk",
    "modern office lobby",
    "suburban family home exterior",
    "waterfront property view",
    "commercial real estate aerial",
    "contemporary kitchen marble",
]

_INTERNAL_KEYS = frozenset({"image_prompt", "unsplash_query"})


class WizardVisualTemplateSpec(BaseModel):
    name: str = Field(default="", max_length=80)
    tag: str = Field(default="", max_length=40)
    bg: str = Field(default="", max_length=500)
    unsplash_query: str = Field(default="", max_length=100)


class WizardVisualTemplatesRoot(BaseModel):
    templates: List[WizardVisualTemplateSpec] = Field(default_factory=list, max_length=12)


def _templates_without_internal_fields(templates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{k: v for k, v in t.items() if k not in _INTERNAL_KEYS} for t in templates]


def _validate_bg(bg: str) -> str:
    fallback = "linear-gradient(135deg,#1e293b 0%,#0f172a 55%,#334155 100%)"
    s = (bg or "").strip()
    low = s.lower()
    if not (
        low.startswith("linear-gradient")
        or low.startswith("radial-gradient")
        or low.startswith("conic-gradient")
        or (low.startswith("#") and len(low) >= 4)
    ):
        return fallback
    return s[:500]


def _normalize_openai_specs(specs: List[WizardVisualTemplateSpec], target_count: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, spec in enumerate(specs[:12]):
        name = (spec.name or "").strip()[:80] or f"Template {i + 1}"
        tag = (spec.tag or "").strip()[:40] or "Template"
        bg = _validate_bg(spec.bg)
        uq = re.sub(r"\s+", " ", (spec.unsplash_query or "").strip())[:100]
        if not uq:
            uq = _PAD_UNSPLASH[i % len(_PAD_UNSPLASH)]
        row: Dict[str, Any] = {
            "id": f"wiz-{i}",
            "name": name,
            "tag": tag,
            "bg": bg,
            "unsplash_query": uq,
        }
        out.append(row)
    j = len(out)
    while len(out) < min(target_count, 12):
        out.append(
            {
                "id": f"wiz-{j}",
                "name": f"Accent {j + 1}",
                "tag": "Template",
                "bg": _PAD_GRADIENTS[j % len(_PAD_GRADIENTS)],
                "unsplash_query": _PAD_UNSPLASH[j % len(_PAD_UNSPLASH)],
            }
        )
        j += 1
    return out


def _unsplash_fallback_queries(
    tpl: Dict[str, Any],
    *,
    bucket: str,
    goal: str,
) -> List[str]:
    bk = (bucket or "creator").strip() or "creator"
    name = str(tpl.get("name") or "").strip()
    tag = str(tpl.get("tag") or "").strip()
    g = re.sub(r"\s+", " ", (goal or "").strip()[:56]).strip()
    primary = str(tpl.get("unsplash_query") or "").strip()
    snippet = re.sub(r"\s+", " ", str(tpl.get("image_prompt") or "")[:120]).strip()
    raw: List[str] = []
    if primary:
        raw.append(primary[:100])
    combo = f"{tag} {name}".strip()
    if combo and combo.lower() != primary.lower():
        raw.append(combo[:100])
    if len(snippet) > 10:
        raw.append(snippet[:100])
    raw.append(f"{bk} real estate interior")
    raw.append(f"{bk} architecture building")
    if g:
        raw.append(f"{bk} {g}"[:100])
    raw.append("professional workspace natural light")
    seen: set[str] = set()
    out: List[str] = []
    for x in raw:
        x = re.sub(r"\s+", " ", x).strip()
        if not x:
            continue
        k = x.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(x[:100])
    return out


async def _attach_unsplash_template_images(
    templates: List[Dict[str, Any]],
    *,
    bucket: str,
    persona_goal: str,
) -> List[Dict[str, Any]]:
    if not os.getenv("UNSPLASH_ACCESS_KEY", "").strip():
        log.warning("wizard_visual_template_unsplash_skipped_no_key")
        return _templates_without_internal_fields(templates)

    from backend.integrations.unsplash import search_photos

    conc = max(1, min(6, int(os.getenv("MANUS_VISUAL_TEMPLATE_UNSPLASH_CONCURRENCY", "3"))))
    sem = asyncio.Semaphore(conc)
    lock = asyncio.Lock()
    used_ids: set[str] = set()

    async def one(tpl: Dict[str, Any]) -> Dict[str, Any]:
        tid = tpl.get("id")
        name = str(tpl.get("name") or "Template").strip()
        tag = str(tpl.get("tag") or "").strip()
        bg = str(tpl.get("bg") or "").strip()
        row: Dict[str, Any] = {"id": tid, "name": name, "tag": tag, "bg": bg}
        queries = _unsplash_fallback_queries(tpl, bucket=bucket, goal=persona_goal)

        async with sem:
            chosen: Optional[Dict[str, Any]] = None
            for q in queries:
                try:
                    photos, _ = await search_photos(q, per_page=15, orientation="squarish")
                except Exception as e:
                    log.warning(
                        "wizard_visual_template_unsplash_search_failed id=%s q=%s err=%s",
                        tid,
                        q[:60],
                        e,
                    )
                    continue
                if not photos:
                    continue
                async with lock:
                    pick: Optional[Dict[str, Any]] = None
                    for p in photos:
                        pid = str(p.get("id") or "").strip()
                        if pid and pid not in used_ids:
                            pick = p
                            break
                    if pick is None:
                        pick = photos[0]
                    pid = str(pick.get("id") or "").strip()
                    if pid:
                        used_ids.add(pid)
                chosen = pick
                break
            if chosen:
                url = str(chosen.get("url") or chosen.get("download_url") or "").strip()
                if url.lower().startswith("https://"):
                    row["image_url"] = url
        return row

    return list(await asyncio.gather(*[one(t) for t in templates]))


def _generate_specs_via_openai(
    *,
    persona: str,
    bucket: str,
    persona_goal: Optional[str],
    brand_summaries: List[Dict[str, Any]],
    count: int,
) -> List[WizardVisualTemplateSpec]:
    _api_key()
    summaries_txt = ""
    if brand_summaries:
        summaries_txt = "\nBrand files on record (names + kinds):\n" + "\n".join(
            f"- {s.get('kind', 'doc')}: {s.get('filename', '')}" for s in brand_summaries[:20]
        )
    goal = (persona_goal or "").strip() or "(not specified)"
    model = (os.getenv("OPENAI_WIZARD_TEMPLATE_MODEL") or "gpt-4o-mini").strip()
    llm = ChatOpenAI(model=model, temperature=0.8, api_key=_api_key()).with_structured_output(
        WizardVisualTemplatesRoot
    )
    human = (
        f"Generate exactly {count} DISTINCT visual templates for Instagram-style square (1:1) post cards.\n\n"
        f"Context:\n"
        f"- persona_id: {persona or 'default'}\n"
        f"- industry_bucket: {bucket or 'creator'}\n"
        f"- stated_goal: {goal}\n"
        f"{summaries_txt}\n\n"
        f"Rules:\n"
        f"- Return exactly {count} items in `templates` (same length).\n"
        f"- Each `unsplash_query`: 2–7 words, concrete Unsplash stock search (architecture, interiors, lifestyle, cityscapes). "
        f"No quotes; no comma-separated keyword lists; vary subjects across templates.\n"
        f"- Each `bg`: one valid CSS linear-gradient or radial-gradient (dark enough that white headline text is readable).\n"
        f"- `name`: 2–5 words, title case. `tag`: short label (e.g. LUXURY, OPEN HOUSE) max 18 characters.\n"
    )
    out: WizardVisualTemplatesRoot = llm.invoke(
        [
            SystemMessage(
                content=(
                    "You are BrokerAI's visual design assistant. You output structured template specs only; "
                    "the app will fetch real Unsplash photographs using your unsplash_query strings."
                )
            ),
            HumanMessage(content=human),
        ]
    )
    return list(out.templates or [])


async def run_wizard_visual_templates(
    *,
    persona: str,
    bucket: str,
    persona_goal: Optional[str],
    brand_summaries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Build template cards: OpenAI (chat) proposes names, gradients, and Unsplash search phrases; Unsplash fills image_url.
    """
    try:
        _api_key()
    except OpenAINotConfiguredError as e:
        raise RuntimeError(str(e)) from e

    count = max(4, min(10, int(os.getenv("MANUS_VISUAL_TEMPLATE_COUNT", "8"))))
    bk = bucket or "creator"

    specs = await asyncio.to_thread(
        _generate_specs_via_openai,
        persona=persona or "default",
        bucket=bk,
        persona_goal=persona_goal,
        brand_summaries=brand_summaries,
        count=count,
    )
    templates = _normalize_openai_specs(specs, count)[:count]
    templates = await _attach_unsplash_template_images(
        templates,
        bucket=bk,
        persona_goal=(persona_goal or "").strip(),
    )
    return {"source": "openai_unsplash", "templates": templates}
