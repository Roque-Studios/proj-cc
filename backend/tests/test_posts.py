"""Unit tests for Post + PostMedia and the creator photo-post upload endpoint.

Acceptance: only creators can create posts (403 for registered, 401 unauthenticated);
a post requires at least one media file; file type/size are validated; the happy
path persists the post + media and serves the files.
"""

from __future__ import annotations

import io

from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.models import Post, PostMedia
from app.storage import get_original_storage

# Fake magic-byte payloads for rejection-path tests (never fetched/served).
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 256
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 256


def _real_jpeg(width: int = 32, height: int = 32) -> bytes:
    """A real decodable JPEG (required: uploads are re-encoded on serve)."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (90, 120, 200)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _real_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 160, 90)).save(buf, format="PNG")
    return buf.getvalue()


def _register(client, email="creator@example.com", password="CreatorPass1"):
    return client.post("/auth/register", json={"email": email, "password": password})


def _login(client, email="creator@example.com", password="CreatorPass1"):
    return client.post("/auth/login", json={"email": email, "password": password})


def _auth_header(client):
    return {"Authorization": f"Bearer {_login(client).json()['access_token']}"}


def _make_creator(client, email="creator@example.com"):
    _register(client, email=email)
    headers = _auth_header(client)
    assert client.post("/creator/apply", headers=headers).status_code == 200
    return headers


# --------------------------------------------------------------------------- #
# Access control
# --------------------------------------------------------------------------- #

def test_create_post_requires_auth(client):
    resp = client.post("/posts", files=[("files", ("a.jpg", JPEG_BYTES, "image/jpeg"))])
    assert resp.status_code == 401


def test_registered_user_cannot_create_post(client):
    _register(client)
    headers = _auth_header(client)
    resp = client.post(
        "/posts", headers=headers,
        files=[("files", ("a.jpg", JPEG_BYTES, "image/jpeg"))],
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# Validation errors
# --------------------------------------------------------------------------- #

def test_post_requires_at_least_one_media_file(client):
    headers = _make_creator(client)
    resp = client.post("/posts", headers=headers)
    assert resp.status_code == 400
    assert "media" in resp.json()["detail"].lower()


def test_rejects_unsupported_extension(client):
    headers = _make_creator(client)
    resp = client.post(
        "/posts", headers=headers,
        files=[("files", ("notes.txt", JPEG_BYTES, "image/jpeg"))],
    )
    assert resp.status_code == 400
    assert "Unsupported file type" in resp.json()["detail"]


def test_rejects_non_image_content_type(client):
    headers = _make_creator(client)
    resp = client.post(
        "/posts", headers=headers,
        files=[("files", ("a.jpg", JPEG_BYTES, "text/plain"))],
    )
    assert resp.status_code == 400
    assert "image" in resp.json()["detail"].lower()


def test_rejects_non_image_content(client):
    """Magic bytes are the authority — a claimed image that isn't one is rejected."""
    headers = _make_creator(client)
    resp = client.post(
        "/posts", headers=headers,
        files=[("files", ("a.jpg", b"definitely not an image", "image/jpeg"))],
    )
    assert resp.status_code == 400
    assert "not a valid image" in resp.json()["detail"]


def test_rejects_header_spoofed_garbage(client):
    """Magic bytes alone aren't enough — the image must actually decode."""
    headers = _make_creator(client)
    spoofed = b"\xff\xd8\xff\xe0" + b"not actually a jpeg body" * 16
    resp = client.post(
        "/posts", headers=headers,
        files=[("files", ("a.jpg", spoofed, "image/jpeg"))],
    )
    assert resp.status_code == 400
    assert "not a valid image" in resp.json()["detail"]


def test_rejects_extension_content_mismatch(client):
    """A .png file containing JPEG bytes fails the extension/content check."""
    headers = _make_creator(client)
    resp = client.post(
        "/posts", headers=headers,
        files=[("files", ("a.png", JPEG_BYTES, "image/png"))],
    )
    assert resp.status_code == 400
    assert "does not match" in resp.json()["detail"]


def test_rejects_oversized_file(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_MEDIA_SIZE_BYTES", 1024)
    headers = _make_creator(client)
    resp = client.post(
        "/posts", headers=headers,
        files=[("files", ("big.jpg", JPEG_BYTES * 10, "image/jpeg"))],
    )
    assert resp.status_code == 413


def test_validation_failure_persists_nothing(client, db_session):
    headers = _make_creator(client)
    client.post(
        "/posts", headers=headers,
        files=[("files", ("bad.txt", JPEG_BYTES, "image/jpeg"))],
    )
    with db_session as db:
        assert db.query(Post).count() == 0
        assert db.query(PostMedia).count() == 0


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #

def test_creator_uploads_photo_post(client, db_session):
    headers = _make_creator(client)
    resp = client.post(
        "/posts",
        headers=headers,
        data={"caption": "Hello followers"},
        files=[
            ("files", ("photo1.jpg", _real_jpeg(), "image/jpeg")),
            ("files", ("photo2.png", _real_png(), "image/png")),
        ],
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["caption"] == "Hello followers"
    assert len(body["media"]) == 2

    with db_session as db:
        creator_id = db.scalar(select(Post.creator_id).where(Post.id == body["id"]))
        assert creator_id is not None
        assert db.query(PostMedia).filter_by(post_id=body["id"]).count() == 2

        # Originals landed in the private store. The public media url is the
        # auth-gated /content endpoint — the storage key never appears in it.
        for media in body["media"]:
            assert media["media_url"].startswith("/content/")
            assert media["media_type"] in ("image/jpeg", "image/png")
            row = db.get(PostMedia, media["id"])
            assert row is not None and get_original_storage().exists(row.storage_key)


def test_blank_caption_is_stored_as_null(client, db_session):
    headers = _make_creator(client)
    resp = client.post(
        "/posts", headers=headers,
        data={"caption": "   "},
        files=[("files", ("photo.jpg", _real_jpeg(), "image/jpeg"))],
    )
    assert resp.status_code == 201
    assert resp.json()["caption"] is None


def test_media_served_after_upload(client):
    """The creator can fetch their own media; it is watermarked + never cached."""
    headers = _make_creator(client)
    original = _real_jpeg()
    resp = client.post(
        "/posts", headers=headers,
        files=[("files", ("photo.jpg", original, "image/jpeg"))],
    )
    media_url = resp.json()["media"][0]["media_url"]

    served = client.get(media_url, headers=headers)
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/jpeg")
    # The served bytes are the on-the-fly watermarked original — transformed,
    # never the original upload itself.
    assert served.content != original
    # Private media is never cached.
    assert "no-store" in served.headers.get("cache-control", "")


def test_media_unknown_id_404(client):
    """Unknown posts/media 404 before any auth check runs."""
    assert client.get("/content/999999/media?media_id=1").status_code == 404


def test_media_watermark_tracks_viewer_via_token(client, db_session):
    """?token= works for <img> tags; the blob traces back to its viewer."""
    headers = _make_creator(client)
    original = _real_jpeg()
    resp = client.post(
        "/posts", headers=headers,
        files=[("files", ("photo.jpg", original, "image/jpeg"))],
    )
    media_url = resp.json()["media"][0]["media_url"]

    # The endpoint requires authentication — no anonymous downgrade anymore.
    assert client.get(media_url).status_code == 401
    assert client.get(f"{media_url}&token=garbage").status_code == 401

    # The creator's own token traces the watermark back to them.
    token = _login(client).json()["access_token"]
    tracked = client.get(f"{media_url}&token={token}")
    assert tracked.status_code == 200
    assert tracked.headers.get("x-watermark", "").startswith("user:")
    assert tracked.content != original  # never the original upload bytes

    # A valid token without a subscription is still blocked (403).
    _register(client, "fan@example.com")
    fan_token = _login(client, "fan@example.com").json()["access_token"]
    assert client.get(f"{media_url}&token={fan_token}").status_code == 403
