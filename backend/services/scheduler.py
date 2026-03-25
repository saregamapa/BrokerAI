"""
Background publish loop — delegates to publish_service (locks, idempotency, retries).
"""
from __future__ import annotations

from backend.services.publish_service import publish_due_posts_workflow

# Backwards-compatible name for main.py lifespan
publish_due_posts = publish_due_posts_workflow
