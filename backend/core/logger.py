"""Central logging configuration for BrokerAI.

Supports two modes:
  - text  (default, human-readable; good for local dev)
  - json  (one JSON object per line; ideal for Render / Datadog / Logtail)

Select via env var: LOG_FORMAT=json (or text). LOG_LEVEL controls verbosity.

Also exposes `log_event(name, **fields)` — a small structured-event helper that
emits a tagged log line with key=value pairs (or a nested JSON payload in JSON
mode). Prefer it for business events (signup, login, publish, ai_call) so logs
stay greppable and alertable.
"""
import json
import logging
import os
import sys
import time
from typing import Any, Dict, Optional

_LOG_LEVEL_NAMES = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _resolve_level() -> int:
    """Return log level from LOG_LEVEL env var, defaulting to INFO."""
    raw = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    return _LOG_LEVEL_NAMES.get(raw, logging.INFO)


def _resolve_format() -> str:
    raw = os.getenv("LOG_FORMAT", "text").strip().lower()
    return "json" if raw == "json" else "text"


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per line. Merges any structured fields on the record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        event = getattr(record, "event", None)
        if event:
            payload["event"] = event
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            for k, v in fields.items():
                if k not in payload:
                    payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        try:
            return json.dumps(payload, default=str, ensure_ascii=False)
        except Exception:
            return json.dumps({"ts": payload["ts"], "level": payload["level"], "msg": payload["msg"]})


def configure_logging(level: Optional[int] = None) -> None:
    """Idempotent setup. Reads LOG_LEVEL + LOG_FORMAT env vars."""
    effective = level if level is not None else _resolve_level()
    root = logging.getLogger()

    if root.handlers:
        root.setLevel(effective)
        for h in root.handlers:
            h.setLevel(effective)
        return

    if _resolve_format() == "json":
        fmt: logging.Formatter = _JsonFormatter()
    else:
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)
    handler.setLevel(effective)
    root.addHandler(handler)
    root.setLevel(effective)

    # Tone down noisy third-party loggers.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name or "brokerai")


# ---------------------------------------------------------------------------
# Structured event helper
# ---------------------------------------------------------------------------

_event_logger = logging.getLogger("brokerai.event")


def _scrub(value: Any) -> Any:
    """Best-effort PII scrubbing: redact most of the email local part."""
    if isinstance(value, str) and "@" in value and "." in value.split("@")[-1]:
        local, _, domain = value.partition("@")
        if local:
            return f"{local[0]}***@{domain}"
    return value


def log_event(name: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """Emit a structured event log line.

    In JSON mode fields become top-level keys; in text mode they're rendered as
    `k=v k=v` after the event name. Use for business events worth alerting on
    (e.g. `log_event("signup", user_id=user.id, account_type="team")`).

    The `email` field, if present, is automatically scrubbed.
    """
    if "email" in fields:
        fields["email"] = _scrub(fields["email"])

    text_bits = " ".join(f"{k}={v}" for k, v in fields.items())
    text_msg = f"event={name} {text_bits}".strip()
    extra = {"event": name, "fields": fields}
    _event_logger.log(level, text_msg, extra=extra)


class time_block:
    """Context manager that logs elapsed time when the block exits.

    Usage:
        with time_block("ai.openai_json", model="gpt-4o-mini") as t:
            await client.chat.completions.create(...)
        # emits: event=ai.openai_json model=gpt-4o-mini duration_ms=482 status=ok
    """

    def __init__(self, name: str, **fields: Any):
        self.name = name
        self.fields = fields
        self._t0 = 0.0

    def __enter__(self) -> "time_block":
        self._t0 = time.perf_counter()
        return self

    def set(self, **fields: Any) -> None:
        self.fields.update(fields)

    def __exit__(self, exc_type, exc, tb) -> None:
        dur_ms = int((time.perf_counter() - self._t0) * 1000)
        status = "error" if exc_type else "ok"
        extras = dict(self.fields)
        if exc_type:
            extras["error"] = f"{exc_type.__name__}: {exc}"
        log_event(
            self.name,
            level=logging.ERROR if exc_type else logging.INFO,
            duration_ms=dur_ms,
            status=status,
            **extras,
        )
        return None
