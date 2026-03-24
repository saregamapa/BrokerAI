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
os.environ.setdefault("OPENAI_API_KEY", "")

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
