"""Blocking Sora video generation for campaign posts (LangGraph media node runs sync)."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from urllib.parse import quote

from backend.core.logger import get_logger
from backend.integrations.openai_sora_video import (
    create_sora_video,
    download_sora_video_bytes,
    fetch_sora_video,
)
from backend.services.wizard_video_asset import (
    create_wizard_video_serve_token,
    ensure_sora_video_materialized,
)

log = get_logger("brokerai.campaign_video")


def _public_origin() -> str:
    return (os.getenv("BROKERAI_PUBLIC_ORIGIN") or "").strip().rstrip("/")


async def _wait_sora_and_serve_url(
    *,
    user_id: int,
    prediction_id: str,
    base_dir: Path,
    public_origin: str,
    poll_s: float = 5.0,
    max_rounds: int = 120,
) -> str:
    pid = (prediction_id or "").strip()
    if not pid:
        return ""
    origin = (public_origin or _public_origin() or "http://127.0.0.1:8000").rstrip("/")
    for _ in range(max(1, max_rounds)):
        res = await fetch_sora_video(pid)
        st = str(res.get("status") or "").lower()
        if st == "succeeded":
            try:
                fn = await ensure_sora_video_materialized(
                    base_dir=base_dir,
                    user_id=int(user_id),
                    openai_video_id=pid,
                    download=download_sora_video_bytes,
                )
                token = create_wizard_video_serve_token(int(user_id), fn)
                return f"{origin}/wizard-video-serve?token={quote(token, safe='')}"
            except Exception as exc:  # noqa: BLE001
                log.warning("campaign_video_materialize_failed id=%s err=%s", pid, exc)
                return ""
        if st == "failed":
            log.warning("campaign_video_sora_failed id=%s err=%s", pid, res.get("error"))
            return ""
        await asyncio.sleep(poll_s)
    log.warning("campaign_video_sora_timeout id=%s", pid)
    return ""


async def generate_campaign_reel_serve_url(
    *,
    user_id: int,
    prompt: str,
    duration_seconds: int,
    aspect_ratio: str,
    base_dir: Path,
    public_origin: str = "",
) -> str:
    """Create a Sora job, poll to completion, materialize MP4, return signed ``/wizard-video-serve`` URL."""
    p = (prompt or "").strip()
    if not p:
        return ""
    start = await create_sora_video(p, duration_seconds, aspect_ratio)
    pid = str(start.get("prediction_id") or "").strip()
    if not pid or str(start.get("status") or "").lower() == "failed":
        return ""
    return await _wait_sora_and_serve_url(
        user_id=user_id,
        prediction_id=pid,
        base_dir=base_dir,
        public_origin=public_origin or _public_origin(),
    )


def generate_campaign_reel_serve_url_sync(
    *,
    user_id: int,
    prompt: str,
    duration_seconds: int,
    aspect_ratio: str,
    base_dir: Path,
    public_origin: str = "",
) -> str:
    """Sync entry for ``media_node`` (worker thread has no running asyncio loop)."""
    try:
        return asyncio.run(
            generate_campaign_reel_serve_url(
                user_id=user_id,
                prompt=prompt,
                duration_seconds=duration_seconds,
                aspect_ratio=aspect_ratio,
                base_dir=base_dir,
                public_origin=public_origin,
            )
        )
    except RuntimeError as e:
        log.warning("campaign_video_asyncio_run_failed: %s", e)
        return ""
