"""OpenAI Sora video generation (Videos API) — wizard preview clips.

Docs: https://platform.openai.com/docs/api-reference/videos/create
Requires OPENAI_API_KEY. Model defaults to ``sora-2``; override with OPENAI_SORA_MODEL.
"""
from __future__ import annotations

import os
from typing import Any, Dict

import httpx

from backend.core.logger import get_logger

log = get_logger("brokerai.openai_sora")

OPENAI_V1 = "https://api.openai.com/v1"


def _token() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip()


def _model() -> str:
    return (os.getenv("OPENAI_SORA_MODEL", "sora-2") or "sora-2").strip()


def duration_to_sora_seconds(duration_seconds: int) -> str:
    """Sora accepts 4, 8, or 12 second clips (string enums in API)."""
    s = max(1, int(duration_seconds))
    if s <= 4:
        return "4"
    if s <= 8:
        return "8"
    return "12"


def aspect_to_size(aspect_ratio: str) -> str:
    a = (aspect_ratio or "9:16").strip()
    if a == "16:9":
        return "1280x720"
    if a == "1:1":
        return "720x1280"
    return "720x1280"


def _normalize_job_status(raw: str) -> str:
    s = (raw or "queued").lower()
    if s == "completed":
        return "succeeded"
    if s == "succeeded":
        return "succeeded"
    if s == "failed":
        return "failed"
    if s in ("in_progress", "processing", "starting"):
        return "processing"
    return "queued"


def _parse_video_response(data: Dict[str, Any]) -> Dict[str, Any]:
    status = _normalize_job_status(str(data.get("status") or ""))
    pid = str(data.get("id") or "")
    err = ""
    e = data.get("error")
    if isinstance(e, dict):
        err = str(e.get("message") or e.get("code") or "").strip()
    elif e:
        err = str(e)
    return {
        "ok": status == "succeeded",
        "status": status,
        "video_url": "",
        "prediction_id": pid,
        "error": err,
    }


async def create_sora_video(
    prompt: str,
    duration_seconds: int = 5,
    aspect_ratio: str = "9:16",
) -> Dict[str, Any]:
    """
    Start a Sora render. Returns the same shape as Replicate helpers:
      { ok, status, video_url, prediction_id, error }
    """
    token = _token()
    if not token:
        return {
            "ok": False,
            "status": "failed",
            "video_url": "",
            "prediction_id": "",
            "error": "OPENAI_API_KEY is not configured",
        }

    model = _model()
    sec = duration_to_sora_seconds(duration_seconds)
    size = aspect_to_size(aspect_ratio)
    url = f"{OPENAI_V1}/videos"
    headers = {"Authorization": f"Bearer {token}"}

    json_body: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "seconds": sec,
        "size": size,
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                url,
                json=json_body,
                headers={**headers, "Content-Type": "application/json"},
            )
            if resp.status_code >= 400:
                # Some accounts expect multipart (curl -F); retry once.
                log.debug(
                    "openai_videos_json_failed status=%s body=%s", resp.status_code, resp.text[:400]
                )
                files = [
                    ("model", (None, model)),
                    ("prompt", (None, prompt)),
                    ("seconds", (None, sec)),
                    ("size", (None, size)),
                ]
                resp = await client.post(url, headers=headers, files=files)
    except httpx.RequestError as e:
        return {
            "ok": False,
            "status": "failed",
            "video_url": "",
            "prediction_id": "",
            "error": f"network_error: {e}",
        }

    if resp.status_code >= 400:
        log.warning("openai_videos_create_failed status=%s body=%s", resp.status_code, resp.text[:400])
        return {
            "ok": False,
            "status": "failed",
            "video_url": "",
            "prediction_id": "",
            "error": f"openai_{resp.status_code}: {resp.text[:300]}",
        }

    data = resp.json() if resp.content else {}
    return _parse_video_response(data)


async def fetch_sora_video(video_id: str) -> Dict[str, Any]:
    token = _token()
    if not token:
        return {
            "ok": False,
            "status": "failed",
            "video_url": "",
            "prediction_id": video_id,
            "error": "OPENAI_API_KEY is not configured",
        }
    url = f"{OPENAI_V1}/videos/{video_id}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, headers=headers)
    except httpx.RequestError as e:
        return {
            "ok": False,
            "status": "failed",
            "video_url": "",
            "prediction_id": video_id,
            "error": f"network_error: {e}",
        }

    if resp.status_code >= 400:
        return {
            "ok": False,
            "status": "failed",
            "video_url": "",
            "prediction_id": video_id,
            "error": f"openai_{resp.status_code}: {resp.text[:300]}",
        }

    return _parse_video_response(resp.json() if resp.content else {})


async def download_sora_video_bytes(video_id: str) -> bytes:
    """Download finished MP4 (variant=video)."""
    token = _token()
    if not token:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    url = f"{OPENAI_V1}/videos/{video_id}/content"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.get(url, headers=headers, params={"variant": "video"})
    if resp.status_code >= 400:
        raise RuntimeError(f"openai_video_content_{resp.status_code}: {resp.text[:240]}")
    return resp.content
