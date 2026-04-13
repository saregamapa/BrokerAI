"""Unsplash proxy — Smart Image Selector for the New Campaign Wizard.

Uses the public Unsplash Search API. Credentials come from env:
  UNSPLASH_ACCESS_KEY  (Client-ID for GET /search/photos)

Docs: https://unsplash.com/documentation#search-photos
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

import httpx

from backend.core.logger import get_logger

log = get_logger("brokerai.unsplash")

UNSPLASH_SEARCH_URL = "https://api.unsplash.com/search/photos"


def _access_key() -> str:
    return os.getenv("UNSPLASH_ACCESS_KEY", "").strip()


async def search_photos(
    query: str,
    per_page: int = 12,
    orientation: str = "landscape",
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Returns (photos, total). photos = [{id,url,thumb_url,download_url,author,author_url}, ...].
    Raises RuntimeError if UNSPLASH_ACCESS_KEY is not configured.
    """
    key = _access_key()
    if not key:
        raise RuntimeError("UNSPLASH_ACCESS_KEY is not configured")

    per_page = max(1, min(int(per_page or 12), 30))
    orientation = orientation if orientation in ("landscape", "portrait", "squarish") else "landscape"

    params = {
        "query": query.strip() or "real estate",
        "per_page": per_page,
        "orientation": orientation,
        "content_filter": "high",
    }
    headers = {"Authorization": f"Client-ID {key}", "Accept-Version": "v1"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(UNSPLASH_SEARCH_URL, params=params, headers=headers)

    if resp.status_code >= 400:
        log.warning("unsplash_search_failed status=%s body=%s", resp.status_code, resp.text[:200])
        raise RuntimeError(f"Unsplash error {resp.status_code}")

    data = resp.json() if resp.content else {}
    results = data.get("results") or []
    photos: List[Dict[str, Any]] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        urls = r.get("urls") or {}
        user = r.get("user") or {}
        user_links = user.get("links") or {}
        photos.append({
            "id": str(r.get("id") or ""),
            "url": str(urls.get("regular") or urls.get("full") or ""),
            "thumb_url": str(urls.get("thumb") or urls.get("small") or ""),
            "download_url": str(urls.get("full") or urls.get("regular") or ""),
            "author": str(user.get("name") or ""),
            "author_url": str(user_links.get("html") or ""),
        })
    total = int(data.get("total") or len(photos))
    return photos, total
