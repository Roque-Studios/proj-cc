"""Pytest fixtures. Imported before any test module.

The auth endpoints run against an isolated SQLite database (the real Postgres
engine is never connected — ``get_db`` is dependency-overridden). App settings
only need *some* valid values, which the container env provides in CI/dev.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("SECRET_KEY", "pytest-insecure-secret-key-for-tests")
os.environ.setdefault("CC_VERSION", "0.1.0")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import token_store
from app.database import Base, get_db
from app.main import app
from app.models import CreatorProfile, User

# Token revocation uses an in-memory denylist (no Redis needed in tests).
token_store.reset_store_for_tests()

TEST_DB_PATH = "/tmp/test_auth.db"
TEST_DB_URL = f"sqlite:///{TEST_DB_PATH}"

_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
_TestSession = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def _override_get_db():
    db = _TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Fresh schema for the whole session."""
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    Base.metadata.create_all(bind=_engine)
    yield


@pytest.fixture(autouse=True)
def _clean_db():
    """Reset the user + creator_profile tables between tests (children first)."""
    yield
    with _TestSession() as db:
        db.query(CreatorProfile).delete()
        db.query(User).delete()
        db.commit()


@pytest.fixture
def db_session():
    with _TestSession() as db:
        yield db


@pytest.fixture
def client():
    return TestClient(app)
