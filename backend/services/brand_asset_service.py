"""Validate and persist wizard brand file uploads (logos, templates, documents)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import List, Optional

ALLOWED_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".docx"})
MAX_BYTES = 25 * 1024 * 1024
ALLOWED_KINDS = frozenset({"logo", "template", "document"})


def normalize_kind(kind: Optional[str]) -> str:
    k = (kind or "document").strip().lower()
    return k if k in ALLOWED_KINDS else "document"


def validate_upload(original_filename: str, size: int) -> str:
    ext = Path(original_filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"File type not allowed. Use one of: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    if size > MAX_BYTES:
        raise ValueError("File too large (max 25 MB per file)")
    if size <= 0:
        raise ValueError("Empty file")
    return ext


def brand_dir_for_user(base_dir: Path, user_id: int) -> Path:
    d = base_dir / "uploads" / "brand" / str(int(user_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_stored_filename(ext: str) -> str:
    return f"{uuid.uuid4().hex}{ext}"


def disk_path(base_dir: Path, user_id: int, stored_filename: str) -> Path:
    """Absolute path to stored file; `stored_filename` must be a plain basename."""
    name = Path(stored_filename).name
    if name != stored_filename or ".." in stored_filename:
        raise ValueError("Invalid stored filename")
    return brand_dir_for_user(base_dir, user_id) / name


def guess_content_type(ext: str) -> str:
    mapping = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    return mapping.get(ext, "application/octet-stream")


def assert_owned_asset_ids(session, user_id: int, asset_ids: Optional[List[int]]) -> None:
    """Raise ValueError if any id is missing or not owned by user."""
    if not asset_ids:
        return
    from backend.models import BrandAsset

    clean = list({int(x) for x in asset_ids if x is not None})
    uid = int(user_id)
    for aid in clean:
        row = session.get(BrandAsset, aid)
        if row is None or int(row.user_id) != uid:
            raise ValueError(f"Unknown or inaccessible brand asset: {aid}")
