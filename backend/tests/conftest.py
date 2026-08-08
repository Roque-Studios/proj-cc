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

from app import cache as cache_module
from app import token_store
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import (
    Conversation,
    CreatorGatewayConfig,
    CreatorProfile,
    Message,
    MessageMedia,
    PaidMessageUnlock,
    PaidUnlock,
    Payment,
    Post,
    PostComment,
    PostLike,
    PostMedia,
    ProcessedWebhookEvent,
    Story,
    StoryMedia,
    Subscription,
    User,
)
from tests.fake_realtime import FakePubSubHub
from tests.fake_redis import FakeRedis

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
    """Reset tables between tests (children first)."""
    yield
    with _TestSession() as db:
        db.query(ProcessedWebhookEvent).delete()
        db.query(PaidUnlock).delete()  # FKs to post + user
        db.query(PaidMessageUnlock).delete()  # FKs to message + user
        db.query(MessageMedia).delete()  # FK to message
        db.query(Payment).delete()  # FKs to user (post_id is intentionally not a FK)
        db.query(StoryMedia).delete()
        db.query(Story).delete()
        db.query(PostLike).delete()  # FKs to post + user
        db.query(PostComment).delete()  # FKs to post + user
        db.query(PostMedia).delete()
        db.query(Post).delete()
        db.query(Message).delete()  # FK to conversation + user
        db.query(Conversation).delete()  # FK to user
        db.query(Subscription).delete()
        db.query(CreatorGatewayConfig).delete()  # FK to user
        db.query(CreatorProfile).delete()
        db.query(User).delete()
        db.commit()


@pytest.fixture(autouse=True)
def _isolated_media_storage(tmp_path, monkeypatch):
    """Uploaded originals land in a per-test temp dir, never the real volume."""
    monkeypatch.setattr(
        settings, "ORIGINAL_MEDIA_STORAGE_PATH", str(tmp_path / "media" / "original")
    )
    monkeypatch.setattr(
        settings, "BANNER_STORAGE_PATH", str(tmp_path / "media" / "banner")
    )
    monkeypatch.setattr(
        settings, "AVATAR_STORAGE_PATH", str(tmp_path / "media" / "avatar")
    )


@pytest.fixture(autouse=True)
def _fake_watermark_cache(monkeypatch):
    """Watermark cache runs on an in-memory fake (no Redis needed in tests).

    ``app.cache._get_client`` returns the module-level ``_client`` once set, so
    installing a fresh ``FakeRedis`` per test fully isolates the cache —
    including TTL expiry, which the fake implements with real wall-clock time.
    """
    fake = FakeRedis()
    monkeypatch.setattr(cache_module, "_client", fake)
    monkeypatch.setattr(cache_module, "_unavailable_logged", False)
    yield fake
    fake.clear()


@pytest.fixture(autouse=True)
def _fake_realtime(monkeypatch):
    """The realtime manager runs on an in-memory pub/sub hub (no Redis).

    Every test gets a fresh manager + hub so sockets and relay tasks never
    leak across tests; the relay task is cancelled at teardown via ``shutdown``.
    """
    from app import realtime as realtime_module

    hub = FakePubSubHub()
    manager = realtime_module.RealtimeManager(
        async_client_factory=lambda: hub.async_client(),
        sync_client_factory=lambda: hub.sync_client(),
    )
    monkeypatch.setattr(realtime_module, "manager", manager)
    yield manager
    manager.shutdown()


@pytest.fixture
def db_session():
    with _TestSession() as db:
        yield db


@pytest.fixture
def client():
    return TestClient(app)
