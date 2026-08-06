"""Tests for the watermark traceability lookup (abuse investigation).

Acceptance: given a watermark text line (as read off a leaked image), the
lookup correctly returns the originating user_id and post_id; access to the
endpoint is restricted to the admin role (the creator role on this platform);
a unit test covers the hash round-trip.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import Post, User, UserRole
from app.watermark import build_watermark_text
from app.watermark_trace import (
    WatermarkTraceError,
    lookup_trace,
    parse_watermark_text,
)

TS = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def test_parse_current_4_field_format():
    token = parse_watermark_text("a1b2c3d4e5 f6a7b8c9d0 2026-08-06T12:00:00 UTC")
    assert token.viewer_hash == "a1b2c3d4e5"
    assert token.post_hash == "f6a7b8c9d0"
    assert token.fetched_at == datetime(2026, 8, 6, 12, 0, 0)


def test_parse_legacy_3_field_format():
    token = parse_watermark_text("a1b2c3d4e5 2026-08-06T12:00:00 UTC")
    assert token.viewer_hash == "a1b2c3d4e5"
    assert token.post_hash is None
    assert token.fetched_at == datetime(2026, 8, 6, 12, 0, 0)


def test_parse_rejects_malformed_text():
    for bad in (
        "",
        "a1b2c3d4e5",                              # too few fields
        "a1b2c3d4e5 f6a7b8c9d0 2026-08-06T12:00:00",  # missing tz marker
        "zzzzzzzzzz f6a7b8c9d0 2026-08-06T12:00:00 UTC",  # bad viewer hash
        "a1b2c3d4e5 zzzzzzzzzz 2026-08-06T12:00:00 UTC",  # bad post hash
        "a1b2c3d4e5 f6a7b8c9d0 2026-13-99T25:99:99 UTC",  # bad timestamp
        "a1b2c3d4e5 f6a7b8c9d0 2026-08-06T12:00:00 GMT",  # bad timezone marker
    ):
        with pytest.raises(WatermarkTraceError):
            parse_watermark_text(bad)


# --------------------------------------------------------------------------- #
# Hash round-trip (acceptance)
# --------------------------------------------------------------------------- #

def _create_creator(db, email: str = "cr@example.com") -> User:
    user = User(
        email=email,
        username=email.split("@")[0],
        hashed_password="not-used-in-tests",
        role=UserRole.creator,
        is_active=True,
        is_creator=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _create_post(db, creator: User, caption: str = "Leaked post") -> Post:
    post = Post(creator_id=creator.id, caption=caption)
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


def test_hash_round_trip_resolves_user_and_post(db_session):
    """Building a watermark and looking it up yields the same user/post ids."""
    db = db_session
    creator = _create_creator(db, "cr@example.com")
    post = _create_post(db, creator, caption="Leaked behind paywall")
    text = build_watermark_text(f"user:{creator.id}", TS, post_id=post.id)

    result = lookup_trace(db, text)
    assert result.user_id == creator.id
    assert result.post_id == post.id
    assert result.user_email == "cr@example.com"
    assert result.post_caption == "Leaked behind paywall"
    assert result.fetched_at == datetime(2026, 8, 6, 12, 0, 0)
    assert result.user_matches == 1
    assert result.post_matches == 1


def test_lookup_unknown_viewer_hash_has_no_user(db_session):
    db = db_session
    creator = _create_creator(db, "cr@example.com")
    _create_post(db, creator, "Leaked post")
    text = build_watermark_text("user:999999", TS, post_id=1)

    result = lookup_trace(db, text)
    assert result.user_id is None
    assert result.user_matches == 0


def test_lookup_legacy_watermark_returns_user_without_post(db_session):
    """Pre-post-identity watermarks decode to the user only (no post hash)."""
    db = db_session
    creator = _create_creator(db, "cr@example.com")
    text = build_watermark_text(f"user:{creator.id}", TS)

    result = lookup_trace(db, text)
    assert result.user_id == creator.id
    assert result.post_id is None
    assert result.post_matches == 0


def test_lookup_tail_deleted_post_has_no_post(db_session):
    """Deleting the highest-id post drops it out of enumeration reach -> no post."""
    db = db_session
    creator = _create_creator(db, "cr@example.com")
    only = _create_post(db, creator, "The only post")
    text = build_watermark_text(f"user:{creator.id}", TS, post_id=only.id)
    db.delete(only)  # tail deletion: the bound falls below the deleted id
    db.commit()

    result = lookup_trace(db, text)
    assert result.user_id == creator.id  # the viewer trace survives
    assert result.post_id is None
    assert result.post_matches == 0


def test_lookup_deleted_post_keeps_user_trace(db_session):
    """A removed post still resolves: id survives (bound kept by newer rows)."""
    db = db_session
    creator = _create_creator(db, "cr@example.com")
    deleted = _create_post(db, creator, "To be deleted")  # id 1
    _create_post(db, creator, "Newer post")  # id 2 — keeps the enumeration bound
    text = build_watermark_text(f"user:{creator.id}", TS, post_id=deleted.id)
    db.delete(deleted)
    db.commit()

    result = lookup_trace(db, text)
    assert result.user_id == creator.id
    assert result.post_id == deleted.id  # the matched id survives row deletion
    assert result.post_caption is None
    assert result.post_matches == 1


# --------------------------------------------------------------------------- #
# Admin endpoint (access restricted to the creator/admin role)
# --------------------------------------------------------------------------- #

def _register(client, email: str, password: str = "AdminCr123"):
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201


def _login(client, email: str, password: str = "AdminCr123") -> dict:
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_creator(client, email: str = "cr@example.com") -> dict:
    _register(client, email)
    headers = _login(client, email)
    assert client.post("/creator/apply", headers=headers).status_code == 200
    return headers


def test_endpoint_requires_admin_creator_role(client, db_session):
    """Anon → 401, registered → 403, creator (admin) → 200 with user+post."""
    creator_headers = _make_creator(client)
    with db_session as db:
        creator = db.scalar(select(User).where(User.email == "cr@example.com"))
        post = _create_post(db, creator, "Leaked post")
        creator_id = creator.id
        post_id = post.id
        text = build_watermark_text(f"user:{creator_id}", TS, post_id=post_id)

    url = "/admin/watermark-trace"
    assert client.get(url, params={"text": text}).status_code == 401

    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    resp = client.get(url, params={"text": text}, headers=fan_headers)
    assert resp.status_code == 403

    resp = client.get(url, params={"text": text}, headers=creator_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == creator_id
    assert body["post_id"] == post_id
    assert body["user_email"] == "cr@example.com"
    assert body["post_caption"] == "Leaked post"
    assert body["fetched_at"] == "2026-08-06T12:00:00"
    assert body["viewer_hash"] == text.split()[0]
    assert body["post_hash"] == text.split()[1]


def test_endpoint_rejects_malformed_watermark(client):
    creator_headers = _make_creator(client)
    resp = client.get(
        "/admin/watermark-trace", params={"text": "not a watermark"}, headers=creator_headers
    )
    assert resp.status_code == 400


def test_endpoint_unknown_viewer_hash_404(client, db_session):
    creator_headers = _make_creator(client)
    with db_session as db:
        creator = db.scalar(select(User).where(User.email == "cr@example.com"))
        _create_post(db, creator, "Leaked post")
    text = build_watermark_text("user:999999", TS, post_id=1)

    resp = client.get("/admin/watermark-trace", params={"text": text}, headers=creator_headers)
    assert resp.status_code == 404
