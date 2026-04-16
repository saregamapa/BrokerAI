"""Resolve a playable campaign / wizard video URL from ``Post.video_script`` JSON."""
from __future__ import annotations

import json
from typing import Any, Dict


def video_url_from_script_json(raw: str) -> str:
    """Return first usable URL from stored JSON (wizard reel, Sora serve URL, legacy keys)."""
    s = (raw or "").strip()
    if not s:
        return ""
    try:
        data: Any = json.loads(s)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    d: Dict[str, Any] = data
    for k in ("video_url", "wizard_external_video_url", "url", "src"):
        u = str(d.get(k) or "").strip()
        if u and u.startswith(("http://", "https://", "/")):
            return u
    return ""
