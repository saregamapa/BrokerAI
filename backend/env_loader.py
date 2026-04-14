"""Load .env before other backend imports that read OPENAI_API_KEY.

Also validates that critical secrets are present (fail-fast in prod mode,
warn in local dev).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")


# Values that indicate the user forgot to set a real secret.
_PLACEHOLDER_VALUES = {
    "",
    "your_key_here",
    "your-key-here",
    "changeme",
    "change-me",
    "replace-me",
    "dev-secret",
    "secret",
    "todo",
    "xxx",
    "xxxx",
    "placeholder",
}


def _is_placeholder(val: str | None) -> bool:
    if val is None:
        return True
    v = val.strip().strip('"').strip("'").lower()
    return v in _PLACEHOLDER_VALUES


def _strict_mode() -> bool:
    """True in prod/staging or when BROKERAI_STRICT_ENV=1."""
    mode = (os.getenv("BROKERAI_ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
    if mode in ("prod", "production", "staging"):
        return True
    return (os.getenv("BROKERAI_STRICT_ENV") or "").strip().lower() in ("1", "true", "yes", "on")


# JWT secret is always required — auth is broken without it.
_REQUIRED_ALWAYS = ["JWT_SECRET_KEY"]
# Strict-only: needed for AI features; local dev can run without them.
_REQUIRED_STRICT = ["OPENAI_API_KEY"]


def validate_required_env(*, raise_on_error: bool | None = None) -> list[str]:
    """Check required env vars. Returns a list of problems (empty = OK).

    In strict/prod mode, raises RuntimeError on problems unless
    raise_on_error is explicitly False.
    """
    problems: list[str] = []

    for key in _REQUIRED_ALWAYS:
        if _is_placeholder(os.getenv(key)):
            problems.append(
                f"{key} is missing or still set to a placeholder. "
                f"Set a strong random value in .env before starting the server."
            )

    if _strict_mode():
        for key in _REQUIRED_STRICT:
            if _is_placeholder(os.getenv(key)):
                problems.append(
                    f"{key} is missing or still set to a placeholder. "
                    f"Set a real API key in .env (strict/prod mode)."
                )

    should_raise = _strict_mode() if raise_on_error is None else raise_on_error
    if problems and should_raise:
        msg = "Environment validation failed:\n  - " + "\n  - ".join(problems)
        print(f"[brokerai] {msg}", file=sys.stderr)
        raise RuntimeError(msg)

    return problems


# Run validation at import time — hard fail in prod, warn in local dev.
_problems = validate_required_env(raise_on_error=None)
if _problems and not _strict_mode():
    for _p in _problems:
        print(f"[brokerai][warn] {_p}", file=sys.stderr)
