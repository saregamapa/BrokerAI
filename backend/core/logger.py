"""Central logging configuration for BrokerAI."""
import logging
import sys
from typing import Optional


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent-ish setup: ensure a single stream handler with a consistent format."""
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name or "brokerai")
