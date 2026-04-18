"""Playwright E2E — registers pytest-playwright and shared options."""

from __future__ import annotations

import pytest

# Load the official Playwright pytest plugin (provides `page`, `context`, etc.).
pytest_plugins = ["pytest_playwright"]


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict) -> dict:
    """Default context for BrokerAI static HTML + API on localhost."""
    return {
        **browser_context_args,
        "viewport": {"width": 1280, "height": 720},
    }
