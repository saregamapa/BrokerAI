"""Persist wizard AI video previews (Sora) and serve via short-lived signed URLs."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Awaitable

from jose import JWTError, jwt

from backend.auth import ALGORITHM


def _signing_key() -> str:
    return os.getenv("JWT_SECRET_KEY", "").strip() or "brokerai-dev-change-me-in-production"


def wizard_video_dir(base_dir: Path, user_id: int) -> Path:
    d = base_dir / "uploads" / "wizard_video" / str(int(user_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_stored_filename(openai_video_id: str) -> str:
    safe = "".join(c for c in openai_video_id if c.isalnum() or c in "_-")[:180] or "video"
    return f"{safe}.mp4"


async def ensure_sora_video_materialized(
    *,
    base_dir: Path,
    user_id: int,
    openai_video_id: str,
    download: Callable[[str], Awaitable[bytes]],
) -> str:
    """Download OpenAI Sora MP4 once per (user, video id); return stored basename."""
    d = wizard_video_dir(base_dir, user_id)
    fname = safe_stored_filename(openai_video_id)
    dest = d / fname
    if not dest.is_file() or dest.stat().st_size == 0:
        raw = await download(openai_video_id)
        if not raw:
            raise RuntimeError("empty_video_body")
        dest.write_bytes(raw)
    return fname


def create_wizard_video_serve_token(user_id: int, stored_basename: str, *, days: int = 7) -> str:
    fn = Path(stored_basename).name
    if fn != stored_basename or ".." in stored_basename:
        raise ValueError("invalid stored basename")
    expire = datetime.utcnow() + timedelta(days=int(days))
    payload = {"wiz": "vid", "sub": str(int(user_id)), "fn": fn, "exp": expire}
    raw = jwt.encode(payload, _signing_key(), algorithm=ALGORITHM)
    if isinstance(raw, bytes):
        return raw.decode("ascii")
    return str(raw)


def decode_wizard_video_serve_token(token: str) -> tuple[int, str]:
    payload = jwt.decode(token, _signing_key(), algorithms=[ALGORITHM])
    if payload.get("wiz") != "vid":
        raise JWTError("wrong token type")
    uid = int(payload.get("sub"))
    fn = str(payload.get("fn") or "")
    if not fn or Path(fn).name != fn:
        raise JWTError("invalid fn")
    return uid, fn
