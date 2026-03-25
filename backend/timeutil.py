"""IANA timezone helpers (scheduling + API validation)."""
from __future__ import annotations

from typing import Optional
from zoneinfo import ZoneInfo


def is_valid_iana_timezone(name: str) -> bool:
    s = (name or "").strip()
    if not s:
        return False
    try:
        ZoneInfo(s)
    except Exception:
        return False
    return True


def normalize_iana_timezone(name: Optional[str], *, fallback: str = "UTC") -> str:
    """Return a valid IANA id; invalid input falls back to ``fallback`` (default UTC)."""
    raw = (name or "").strip() or fallback
    try:
        ZoneInfo(raw)
        return raw
    except Exception:
        try:
            ZoneInfo(fallback)
            return fallback
        except Exception:
            return "UTC"
