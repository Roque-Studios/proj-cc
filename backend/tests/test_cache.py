"""Tests for the per-viewer watermark cache layer (Redis + TTL).

Acceptance: the second request for the same (viewer, media) is served from the
cache (asserted via the ``X-Watermark-Cache`` header and a render spy — no
re-render happens); entries evict after the TTL; cache hit/miss paths are
covered. The cache runs on ``tests.fake_redis.FakeRedis`` (installed autouse in
conftest) so no Redis server is needed — TTL expiry is real wall-clock time.
"""

from __future__ import annotations

import io
import time
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image
from sqlalchemy import select

from app import cache as cache_module
from app import media as media_module
from app.config import settings
from app.models import Subscription, SubscriptionStatus, User


def _real_jpeg() -> bytes:
    # Large enough that the per-viewer watermark is visibly different (the
    # watermark text is wider than tiny images, which would render identically
    # for different viewers within the same second).
    buf = io.BytesIO()
    Image.new("RGB", (320, 240), (30, 120, 210)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _register(client, email: str, password: str = "CacheCr123"):
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201


def _login(client, email: str, password: str = "CacheCr123"):
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _upload_media(client, db) -> tuple[str, str]:
    """Create a creator with a photo post + a subscribed fan.

    Returns ``(media_url, fan_token)`` — the fan is an active follower who can
    fetch the media through the auth-gated content endpoint.
    """
    _register(client, "cr@example.com")
    token = _login(client, "cr@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/creator/apply", headers=headers).status_code == 200
    resp = client.post(
        "/posts",
        headers=headers,
        files=[("files", ("photo.jpg", _real_jpeg(), "image/jpeg"))],
    )
    assert resp.status_code == 201
    post = resp.json()

    _register(client, "fan@example.com", "CacheFan123")
    fan_token = _login(client, "fan@example.com", "CacheFan123")
    with db:
        fan = db.scalar(select(User).where(User.email == "fan@example.com"))
        creator = db.scalar(select(User).where(User.email == "cr@example.com"))
        db.add(
            Subscription(
                subscriber_id=fan.id,
                creator_id=creator.id,
                status=SubscriptionStatus.active,
                current_period_start=datetime.now(timezone.utc) - timedelta(days=1),
                current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
                payment_provider="mock",
                external_ref=f"sub_cache_{fan.id}",
            )
        )
        db.commit()
    return post["media"][0]["media_url"], fan_token


def _make_second_fan(client, db) -> str:
    """A second subscribed fan (distinct viewer identity) -> access token."""
    _register(client, "fan2@example.com", "CacheFan123")
    fan_token = _login(client, "fan2@example.com", "CacheFan123")
    with db:
        fan = db.scalar(select(User).where(User.email == "fan2@example.com"))
        creator = db.scalar(select(User).where(User.email == "cr@example.com"))
        db.add(
            Subscription(
                subscriber_id=fan.id,
                creator_id=creator.id,
                status=SubscriptionStatus.active,
                current_period_start=datetime.now(timezone.utc) - timedelta(days=1),
                current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
                payment_provider="mock",
                external_ref=f"sub_cache2_{fan.id}",
            )
        )
        db.commit()
    return fan_token


# --------------------------------------------------------------------------- #
# Hit / miss paths (endpoint level)
# --------------------------------------------------------------------------- #

def test_second_request_served_from_cache(client, db_session):
    media_url, fan_token = _upload_media(client, db_session)
    url = f"{media_url}&token={fan_token}"

    first = client.get(url)
    assert first.status_code == 200
    assert first.headers.get("x-watermark-cache") == "miss"
    assert first.headers.get("x-watermark").startswith("user:")

    second = client.get(url)
    assert second.status_code == 200
    assert second.headers.get("x-watermark-cache") == "hit"
    # Cache hit serves the exact same bytes — no re-render.
    assert second.content == first.content


def test_cache_is_separated_per_viewer(client, db_session):
    media_url, fan1_token = _upload_media(client, db_session)
    fan2_token = _make_second_fan(client, db_session)
    url1 = f"{media_url}&token={fan1_token}"
    url2 = f"{media_url}&token={fan2_token}"

    f1_first = client.get(url1)
    assert f1_first.headers.get("x-watermark-cache") == "miss"
    f2_first = client.get(url2)
    assert f2_first.headers.get("x-watermark-cache") == "miss"
    # Different viewer, different watermark bytes.
    assert f2_first.content != f1_first.content

    # Each viewer now hits their own entry.
    assert client.get(url1).headers.get("x-watermark-cache") == "hit"
    assert client.get(url2).headers.get("x-watermark-cache") == "hit"


def test_cache_evicts_after_ttl(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "WATERMARK_CACHE_TTL_SECONDS", 1)
    media_url, fan_token = _upload_media(client, db_session)
    url = f"{media_url}&token={fan_token}"

    assert client.get(url).headers.get("x-watermark-cache") == "miss"
    assert client.get(url).headers.get("x-watermark-cache") == "hit"

    time.sleep(1.2)  # TTL elapsed -> Redis evicts the entry
    assert client.get(url).headers.get("x-watermark-cache") == "miss"


def test_render_happens_once_per_viewer(client, db_session, monkeypatch):
    """A render spy proves the second request skips the watermark pipeline."""
    media_url, fan1_token = _upload_media(client, db_session)
    fan2_token = _make_second_fan(client, db_session)

    real_render = media_module.render_served_media
    calls = {"n": 0}

    def spy_render(original, user_ref, timestamp=None, **kwargs):
        calls["n"] += 1
        return real_render(original, user_ref, timestamp, **kwargs)

    monkeypatch.setattr(media_module, "render_served_media", spy_render)

    client.get(f"{media_url}&token={fan1_token}")
    client.get(f"{media_url}&token={fan1_token}")  # cache hit — must NOT re-render
    assert calls["n"] == 1

    client.get(f"{media_url}&token={fan2_token}")  # different viewer -> re-render
    assert calls["n"] == 2


def test_redis_outage_degrades_to_render(client, db_session, monkeypatch):
    """A Redis failure must not fail media serving — it falls back to a miss."""

    class _BrokenRedis:
        def get(self, *a, **k):
            raise ConnectionError("redis is down")

        def set(self, *a, **k):
            raise ConnectionError("redis is down")

        def delete(self, *a, **k):
            raise ConnectionError("redis is down")

        def ttl(self, *a, **k):
            raise ConnectionError("redis is down")

    monkeypatch.setattr(cache_module, "_get_client", lambda: _BrokenRedis())
    media_url, fan_token = _upload_media(client, db_session)

    resp = client.get(f"{media_url}&token={fan_token}")
    assert resp.status_code == 200
    assert resp.headers.get("x-watermark").startswith("user:")
    assert resp.headers.get("x-watermark-cache") == "miss"
    assert resp.content != _real_jpeg()  # still watermarked, never the original


# --------------------------------------------------------------------------- #
# Cache module unit tests
# --------------------------------------------------------------------------- #

def test_module_roundtrip_and_viewer_keys():
    cache_module.set_watermarked_media("user:1", "key1", b"wm-bytes-A")
    assert cache_module.get_cached_watermarked_media("user:1", "key1") == b"wm-bytes-A"
    # Different viewer or media -> separate entries (miss).
    assert cache_module.get_cached_watermarked_media("user:2", "key1") is None
    assert cache_module.get_cached_watermarked_media("user:1", "key2") is None

    remaining = cache_module.get_cached_media_ttl("user:1", "key1")
    assert 0 < remaining <= settings.WATERMARK_CACHE_TTL_SECONDS

    cache_module.delete_watermarked_media("user:1", "key1")
    assert cache_module.get_cached_watermarked_media("user:1", "key1") is None
    assert cache_module.get_cached_media_ttl("user:1", "key1") == -2


def test_module_entry_expires_after_ttl():
    cache_module.set_watermarked_media("anon", "key2", b"wm", ttl_seconds=1)
    assert cache_module.get_cached_watermarked_media("anon", "key2") == b"wm"
    time.sleep(1.2)
    assert cache_module.get_cached_watermarked_media("anon", "key2") is None
    assert cache_module.get_cached_media_ttl("anon", "key2") == -2
