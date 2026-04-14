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

# Disable slowapi response-header injection for tests. TestClient + Pydantic
# response_model returns a bare model (not a Response), which slowapi 0.1.9
# refuses to decorate. Keeping the limiter enabled but silencing header
# injection keeps rate-limit logic testable without the crash.
_tlim = getattr(app.state, "limiter", None)
if _tlim is not None:
    try:
        _tlim._headers_enabled = False  # type: ignore[attr-defined]
        _tlim.enabled = False  # disable per-endpoint limits during tests
    except Exception:
        pass


@pytest.fixture(scope="session", autouse=True)
def _init_database():
    create_db_and_tables()
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_ayrshare_profile_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid real Ayrshare calls during tests."""
    from backend.services import ayrshare_service
    import backend.main as main_mod

    real_fetch = ayrshare_service.fetch_active_social_accounts
    real_profiles = ayrshare_service.fetch_profiles_by_ref_id

    def _fetch(pk: str):
        if (pk or "").strip() == "pytest-ayrshare-profile-key":
            return ["facebook", "instagram", "linkedin"]
        return real_fetch(pk)

    def _profiles(ref_id: str):
        if str(ref_id or "").strip().startswith("brokerai_user_"):
            return [{"refId": ref_id, "title": "Pytest Profile"}]
        return real_profiles(ref_id)

    monkeypatch.setattr(ayrshare_service, "fetch_active_social_accounts", _fetch)
    monkeypatch.setattr(ayrshare_service, "fetch_profiles_by_ref_id", _profiles)
    # backend.main imports service functions directly; patch bound references too.
    monkeypatch.setattr(main_mod, "fetch_profiles_by_ref_id", _profiles)
    if hasattr(main_mod, "fetch_active_social_accounts"):
        monkeypatch.setattr(main_mod, "fetch_active_social_accounts", _fetch)
