"""Pytest setup: test DB + env before any backend import."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Must run before importing backend.db / backend.main
_test_db = Path(tempfile.gettempdir()) / f"brokerai_pytest_{os.getpid()}.db"
try:
    _test_db.unlink(missing_ok=True)
except OSError:
    pass

os.environ["DATABASE_URL"] = f"sqlite:///{_test_db.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret-key-32-characters-minimum"
os.environ["BROKERAI_DISABLE_SCHEDULER"] = "1"
# Required by API guard; LangGraph is mocked in most tests via stub_run_campaign_phase1.
os.environ.setdefault(
    "OPENAI_API_KEY",
    "sk-test-openai-key-for-pytest-only-not-a-real-secret-0001",
)

import pytest
from fastapi.testclient import TestClient

from backend.db import create_db_and_tables
from backend.main import app


@pytest.fixture(scope="session", autouse=True)
def _init_database():
    create_db_and_tables()
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_ayrshare_profile_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid real GET /user during tests; keep pytest profile marked as linked after sync."""
    from backend.services import ayrshare_service

    real_fetch = ayrshare_service.fetch_active_social_accounts

    def _fetch(pk: str):
        if (pk or "").strip() == "pytest-ayrshare-profile-key":
            return ["facebook", "instagram", "linkedin"]
        return real_fetch(pk)

    monkeypatch.setattr(ayrshare_service, "fetch_active_social_accounts", _fetch)
