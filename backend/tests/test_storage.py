"""Unit tests for the original-media storage layer.

Acceptance: original (unwatermarked) files are stored behind a storage
abstraction, are never reachable via any public URL, and only internal service
code can read them. Covers the save/read roundtrip plus the privacy guarantees.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.models import PostMedia, Subscription, SubscriptionStatus, User
from app.storage import DiskMediaStorage, MediaStorage, StorageError, get_original_storage

# Real decodable JPEG (uploads are re-encoded on serve, so tests that fetch the
# served media need a real image).
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 128


def _real_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (80, 40, 220)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Storage abstraction roundtrip
# --------------------------------------------------------------------------- #

def test_roundtrip_save_read(tmp_path):
    storage: MediaStorage = DiskMediaStorage(tmp_path)
    storage.save("abc123.jpg", JPEG_BYTES)
    assert storage.read("abc123.jpg") == JPEG_BYTES
    assert storage.exists("abc123.jpg")


def test_save_overwrites(tmp_path):
    storage: MediaStorage = DiskMediaStorage(tmp_path)
    storage.save("k.jpg", b"one")
    storage.save("k.jpg", b"two")
    assert storage.read("k.jpg") == b"two"


def test_exists_and_delete(tmp_path):
    storage: MediaStorage = DiskMediaStorage(tmp_path)
    assert storage.exists("k.jpg") is False
    storage.save("k.jpg", JPEG_BYTES)
    assert storage.exists("k.jpg") is True
    storage.delete("k.jpg")
    assert storage.exists("k.jpg") is False
    storage.delete("k.jpg")  # deleting a missing key is a no-op


def test_read_missing_key_raises(tmp_path):
    storage: MediaStorage = DiskMediaStorage(tmp_path)
    with pytest.raises(StorageError):
        storage.read("nope.jpg")


def test_rejects_path_traversal(tmp_path):
    storage: MediaStorage = DiskMediaStorage(tmp_path)
    for bad_key in ("../escape.jpg", "a/../b.jpg", "..", ".", "dir/file.jpg", "a\\b.jpg"):
        with pytest.raises(StorageError):
            storage.save(bad_key, JPEG_BYTES)
        with pytest.raises(StorageError):
            storage.read(bad_key)


# --------------------------------------------------------------------------- #
# Originals are private: not served, internal-only reads
# --------------------------------------------------------------------------- #

def test_originals_private_and_watermarked_on_serve(client, db_session):
    """Originals stay in the private store; served bytes are always watermarked."""
    _register(client, "cr@example.com")
    headers = _login(client, "cr@example.com")
    client.post("/creator/apply", headers=headers)

    original = _real_jpeg()
    resp = client.post(
        "/posts",
        headers=headers,
        files=[("files", ("photo.jpg", original, "image/jpeg"))],
    )
    assert resp.status_code == 201
    post = resp.json()
    with db_session as db:
        key = db.get(PostMedia, post["media"][0]["id"]).storage_key

    storage = get_original_storage()
    assert storage.exists(key)  # private original preserved
    assert storage.read(key) == original

    # Media serving requires authentication — anonymous is blocked outright.
    assert client.get(post["media"][0]["media_url"]).status_code == 401

    # What authorized viewers get is the on-the-fly watermarked transformation
    # — never the original upload bytes.
    fan_headers = _make_follower(client, db_session, "cr@example.com")
    served = client.get(post["media"][0]["media_url"], headers=fan_headers)
    assert served.status_code == 200
    assert served.content != original
    assert "no-store" in served.headers.get("cache-control", "")
    # The private store root exists and is not routed publicly.
    assert settings.ORIGINAL_MEDIA_STORAGE_PATH


def test_originals_not_reachable_via_public_url(client, db_session):
    """No public URL can fetch an original: only the served copy is routable."""
    _register(client, "cr@example.com")
    headers = _login(client, "cr@example.com")
    client.post("/creator/apply", headers=headers)

    resp = client.post(
        "/posts",
        headers=headers,
        files=[("files", ("photo.jpg", _real_jpeg(), "image/jpeg"))],
    )
    post = resp.json()
    with db_session as db:
        key = db.get(PostMedia, post["media"][0]["id"]).storage_key

    # The advertised url serves the watermarked copy (200) to an authorized
    # viewer…
    fan_headers = _make_follower(client, db_session, "cr@example.com")
    served = client.get(post["media"][0]["media_url"], headers=fan_headers)
    assert served.status_code == 200
    assert served.content != _real_jpeg()  # never the raw original bytes

    # …but the private storage path has no route: any URL-shaped attempt 404s,
    # including the legacy key-based /media route (now closed).
    assert client.get(f"/content/original/{key}").status_code == 404
    assert client.get("/content/original/").status_code == 404
    assert client.get(f"/original/{key}").status_code == 404
    assert client.get(f"/static/{key}").status_code == 404
    assert client.get(f"/media/{key}").status_code == 404


def _register(client, email: str, password: str = "StorCr123"):
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201


def _login(client, email: str, password: str = "StorCr123"):
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_follower(client, db_session, creator_email: str) -> dict:
    """Subscribe a fan to the creator; returns the fan's auth headers."""
    _register(client, "fan@example.com", password="StorFan123")
    fan_token = client.post(
        "/auth/login",
        json={"email": "fan@example.com", "password": "StorFan123"},
    ).json()["access_token"]
    with db_session as db:
        fan = db.scalar(select(User).where(User.email == "fan@example.com"))
        creator = db.scalar(select(User).where(User.email == creator_email))
        db.add(
            Subscription(
                subscriber_id=fan.id,
                creator_id=creator.id,
                status=SubscriptionStatus.active,
                current_period_start=datetime.now(timezone.utc) - timedelta(days=1),
                current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
                payment_provider="mock",
                external_ref=f"sub_storage_{fan.id}",
            )
        )
        db.commit()
    return {"Authorization": f"Bearer {fan_token}"}
