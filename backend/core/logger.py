"""Central logging configuration for BrokerAI."""
import logging
import os
import sys
from typing import Optional

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


def configure_logging(level: int | None = None) -> None:
    """Idempotent-ish setup: ensure a single stream handler with a consistent format.

    Reads LOG_LEVEL env var (DEBUG, INFO, WARNING, ERROR, CRITICAL) when *level* is
    not supplied explicitly.
    """
    effective = level if level is not None else _resolve_level()
    root = logging.getLogger()

    if root.handlers:
        # Already configured — just update level on root + all existing handlers
        root.setLevel(effective)
        for h in root.handlers:
            h.setLevel(effective)
        return

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)
    handler.setLevel(effective)
    root.addHandler(handler)
    root.setLevel(effective)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name or "brokerai")
