"""Replicate AI video generator — powers the wizard's Video content type.

One API token unlocks multiple models. Default is `luma/ray-flash-2-540p`
(~$0.05 per ~5s 540p clip). Swap via REPLICATE_VIDEO_MODEL without code change.

Docs: https://replicate.com/docs/reference/http
"""
from __future__ import annotations

import os
from typing import Any, Dict

import httpx

from backend.core.logger import get_logger

log = get_logger("brokerai.replicate")

REPLICATE_BASE = "https://api.replicate.com/v1"


def _token() -> str:
    return os.getenv("REPLICATE_API_TOKEN", "").strip()


def _model() -> str:
    return os.getenv("REPLICATE_VIDEO_MODEL", "luma/ray-flash-2-540p").strip() or "luma/ray-flash-2-540p"


def _map_input(prompt: str, duration_seconds: int, aspect_ratio: str) -> Dict[str, Any]:
    """Most text-to-video models accept `prompt` + some form of duration/aspect."""
    return {
        "prompt": prompt,
        "duration": int(duration_seconds),
        "aspect_ratio": aspect_ratio,
    }


async def create_video_prediction(
    prompt: str,
    duration_seconds: int = 5,
    aspect_ratio: str = "9:16",
) -> Dict[str, Any]:
    """
    Kick off a prediction on Replicate. Returns dict:
      { ok: bool, status: str, video_url: str, prediction_id: str, error: str }

    We poll briefly (up to ~25s) so the wizard feels responsive; longer jobs
    return prediction_id so the frontend can poll GET /video/status/{id}.
    """
    token = _token()
    if not token:
        return {"ok": False, "status": "failed", "video_url": "", "prediction_id": "",
                "error": "REPLICATE_API_TOKEN is not configured"}

    model = _model()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": "wait=25",  # server-side wait up to 25s before returning
    }
    payload = {"input": _map_input(prompt, duration_seconds, aspect_ratio)}

    url = f"{REPLICATE_BASE}/models/{model}/predictions"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.RequestError as e:
        return {"ok": False, "status": "failed", "video_url": "", "prediction_id": "",
                "error": f"network_error: {e}"}

    if resp.status_code >= 400:
        log.warning("replicate_create_failed status=%s body=%s", resp.status_code, resp.text[:300])
        return {"ok": False, "status": "failed", "video_url": "", "prediction_id": "",
                "error": f"replicate_{resp.status_code}: {resp.text[:200]}"}

    data = resp.json() if resp.content else {}
    return _parse(data)


async def fetch_prediction(prediction_id: str) -> Dict[str, Any]:
    token = _token()
    if not token:
        return {"ok": False, "status": "failed", "video_url": "", "prediction_id": prediction_id,
                "error": "REPLICATE_API_TOKEN is not configured"}

    headers = {"Authorization": f"Bearer {token}"}
    url = f"{REPLICATE_BASE}/predictions/{prediction_id}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
    except httpx.RequestError as e:
        return {"ok": False, "status": "failed", "video_url": "", "prediction_id": prediction_id,
                "error": f"network_error: {e}"}

    if resp.status_code >= 400:
        return {"ok": False, "status": "failed", "video_url": "", "prediction_id": prediction_id,
                "error": f"replicate_{resp.status_code}: {resp.text[:200]}"}
    return _parse(resp.json() if resp.content else {})


def _parse(data: Dict[str, Any]) -> Dict[str, Any]:
    status = str(data.get("status") or "").lower() or "queued"
    pid = str(data.get("id") or "")
    output = data.get("output")
    video_url = ""
    if isinstance(output, str):
        video_url = output
    elif isinstance(output, list) and output:
        first = output[0]
        if isinstance(first, str):
            video_url = first
    err = data.get("error") or ""
    ok = status == "succeeded" and bool(video_url)
    return {
        "ok": ok,
        "status": status if status in ("queued", "starting", "processing", "succeeded", "failed", "canceled") else "queued",
        "video_url": video_url,
        "prediction_id": pid,
        "error": str(err or ""),
    }
