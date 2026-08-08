"""Integration tests for 24-hour stories.

Acceptance:
- only creators can publish stories (401 anonymous, 403 registered);
- a story requires at least one image; validation mirrors posts;
- stories expire 24h after creation — expired stories vanish from the
  follower listing AND media serving (404);
- listing is follower-only (401 anonymous, 403 registered, 200 follower);
- the creator always has access to their own stories (list + media);
- media is watermarked + never cached, exactly like post media;
- the public landing payload's ``has_active_story`` is True only while a live
  story exists (and False after expiry / for creators with none);
- the creator dashboard lists own stories (expired included) and can delete.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from PIL import Image
from sqlalchemy import select

from app.models import Story, Subscription, SubscriptionStatus, User

STORY_TTL = timedelta(hours=24)


def _real_jpeg(width: int = 32, height: int = 32) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 80, 200)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _register(client, email: str, password: str = "StoryCr123"):
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201


def _login(client, email: str, password: str = "StoryCr123"):
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_creator(client, email: str = "storycr@example.com"):
    _register(client, email)
    headers = _login(client, email)
    assert client.post("/creator/apply", headers=headers).status_code == 200
    return headers


def _upload_story(client, headers, caption: str = None, files=None) -> dict:
    resp = client.post(
        "/stories",
        headers=headers,
        data={"caption": caption} if caption else None,
        files=files or [("files", ("story.jpg", _real_jpeg(), "image/jpeg"))],
    )
    assert resp.status_code == 201
    return resp.json()


def _user_id(db, email: str) -> int:
    return db.scalar(select(User.id).where(User.email == email))


def _follow(db, subscriber: User, creator: User, *, days: int = 30) -> Subscription:
    sub = Subscription(
        subscriber_id=subscriber.id,
        creator_id=creator.id,
        status=SubscriptionStatus.active,
        current_period_start=datetime.now(timezone.utc) - timedelta(days=1),
        current_period_end=datetime.now(timezone.utc) + timedelta(days=days),
        payment_provider="mock",
        external_ref=f"sub_story_{subscriber.id}_{creator.id}",
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


# --------------------------------------------------------------------------- #
# Publishing (access + validation)
# --------------------------------------------------------------------------- #

def test_story_requires_auth(client):
    resp = client.post("/stories", files=[("files", ("a.jpg", _real_jpeg(), "image/jpeg"))])
    assert resp.status_code == 401


def test_registered_user_cannot_publish_story(client):
    _register(client, "plain@example.com")
    headers = _login(client, "plain@example.com")
    resp = client.post(
        "/stories", headers=headers,
        files=[("files", ("a.jpg", _real_jpeg(), "image/jpeg"))],
    )
    assert resp.status_code == 403


def test_story_requires_at_least_one_media_file(client):
    headers = _make_creator(client)
    resp = client.post("/stories", headers=headers)
    assert resp.status_code == 400
    assert "media" in resp.json()["detail"].lower()


def test_story_rejects_non_image(client):
    headers = _make_creator(client)
    resp = client.post(
        "/stories", headers=headers,
        files=[("files", ("notes.txt", b"hello", "text/plain"))],
    )
    assert resp.status_code == 400


def test_story_expires_24h_from_now(client, db_session):
    headers = _make_creator(client)
    story = _upload_story(client, headers, caption="Behind the scenes")

    expires_at = datetime.fromisoformat(story["expires_at"])
    created_at = datetime.fromisoformat(story["created_at"])
    # 24h from creation, within a second of slack (SQLite stores
    # ``created_at`` at second precision and the DB stamps it a moment after
    # the Python-computed ``expires_at``).
    assert abs((expires_at - created_at) - STORY_TTL) <= timedelta(seconds=1)
    # And it is in the future — the story is live right now (SQLite returns
    # naive datetimes; normalize before comparing).
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    assert expires_at > datetime.now(timezone.utc)
    assert story["caption"] == "Behind the scenes"
    assert len(story["media"]) == 1
    assert story["media"][0]["media_url"].startswith("/stories/")

    with db_session as db:
        row = db.get(Story, story["id"])
        assert row is not None and row.creator_id is not None


# --------------------------------------------------------------------------- #
# Follower-only listing
# --------------------------------------------------------------------------- #

def test_story_listing_anonymous_401(client):
    creator_headers = _make_creator(client)
    story = _upload_story(client, creator_headers)
    assert client.get(f"/stories/{story['creator_id']}").status_code == 401


def test_story_listing_registered_403(client):
    creator_headers = _make_creator(client)
    story = _upload_story(client, creator_headers)
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    resp = client.get(f"/stories/{story['creator_id']}", headers=fan_headers)
    assert resp.status_code == 403
    assert "follower" in resp.json()["detail"].lower()


def test_story_listing_follower_200(client, db_session):
    creator_headers = _make_creator(client)
    story = _upload_story(client, creator_headers, caption="For my fans")
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "fan@example.com"))
        creator = db.get(User, _user_id(db, "storycr@example.com"))
        _follow(db, fan, creator)

    resp = client.get(f"/stories/{story['creator_id']}", headers=fan_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["caption"] == "For my fans"
    assert body[0]["media"][0]["media_url"].startswith("/stories/")


def test_story_listing_owner_200(client):
    creator_headers = _make_creator(client)
    story = _upload_story(client, creator_headers)
    resp = client.get(f"/stories/{story['creator_id']}", headers=creator_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_expired_subscription_gets_403(client, db_session):
    """An expired subscription classifies as registered -> 403, not follower."""
    creator_headers = _make_creator(client)
    story = _upload_story(client, creator_headers)
    _register(client, "exfan@example.com")
    fan_headers = _login(client, "exfan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "exfan@example.com"))
        creator = db.get(User, _user_id(db, "storycr@example.com"))
        _follow(db, fan, creator, days=-5)

    resp = client.get(f"/stories/{story['creator_id']}", headers=fan_headers)
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# 24h expiry
# --------------------------------------------------------------------------- #

def _force_expire(db, story_id: int) -> None:
    story = db.get(Story, story_id)
    story.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()


def test_expired_story_vanish_from_listing(client, db_session):
    creator_headers = _make_creator(client)
    story = _upload_story(client, creator_headers)
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "fan@example.com"))
        creator = db.get(User, _user_id(db, "storycr@example.com"))
        _follow(db, fan, creator)
        _force_expire(db, story["id"])

    resp = client.get(f"/stories/{story['creator_id']}", headers=fan_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_expired_story_media_404(client, db_session):
    creator_headers = _make_creator(client)
    story = _upload_story(client, creator_headers)
    media_url = story["media"][0]["media_url"]
    with db_session as db:
        _force_expire(db, story["id"])

    resp = client.get(media_url, headers=creator_headers)
    assert resp.status_code == 404
    assert "expired" in resp.json()["detail"].lower()


# --------------------------------------------------------------------------- #
# Media serving
# --------------------------------------------------------------------------- #

def test_story_media_served_to_follower_watermarked(client, db_session):
    creator_headers = _make_creator(client)
    original = _real_jpeg()
    resp = client.post(
        "/stories", headers=creator_headers,
        files=[("files", ("story.jpg", original, "image/jpeg"))],
    )
    media_url = resp.json()["media"][0]["media_url"]
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "fan@example.com"))
        creator = db.get(User, _user_id(db, "storycr@example.com"))
        _follow(db, fan, creator)

    served = client.get(media_url, headers=fan_headers)
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/jpeg")
    assert served.content != original  # watermarked transform, never the original
    assert "no-store" in served.headers.get("cache-control", "")
    assert served.headers.get("x-watermark", "").startswith("user:")


def test_story_media_anonymous_401(client):
    creator_headers = _make_creator(client)
    story = _upload_story(client, creator_headers)
    media_url = story["media"][0]["media_url"]
    assert client.get(media_url).status_code == 401


def test_story_media_registered_403(client):
    creator_headers = _make_creator(client)
    story = _upload_story(client, creator_headers)
    media_url = story["media"][0]["media_url"]
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    assert client.get(media_url, headers=fan_headers).status_code == 403


def test_story_media_unknown_story_404(client):
    assert client.get("/stories/999999/media?media_id=1").status_code == 404


# --------------------------------------------------------------------------- #
# Landing payload story badge
# --------------------------------------------------------------------------- #

def test_landing_has_active_story_true(client, db_session):
    creator_headers = _make_creator(client)
    story = _upload_story(client, creator_headers)

    landing = client.get(f"/creators/{story['creator_id']}/landing")
    assert landing.status_code == 200
    assert landing.json()["profile"]["has_active_story"] is True


def test_landing_no_story_flag_false(client, db_session):
    """A creator with no story reports ``has_active_story`` False."""
    _register(client, "emptycr@example.com")
    empty_headers = _login(client, "emptycr@example.com")
    assert client.post("/creator/apply", headers=empty_headers).status_code == 200
    with db_session as db:
        empty_id = _user_id(db, "emptycr@example.com")

    landing = client.get(f"/creators/{empty_id}/landing")
    assert landing.status_code == 200
    assert landing.json()["profile"]["has_active_story"] is False


def test_landing_flag_false_after_expiry(client, db_session):
    creator_headers = _make_creator(client)
    story = _upload_story(client, creator_headers)
    with db_session as db:
        _force_expire(db, story["id"])

    landing = client.get(f"/creators/{story['creator_id']}/landing")
    assert landing.status_code == 200
    assert landing.json()["profile"]["has_active_story"] is False


# --------------------------------------------------------------------------- #
# Creator dashboard (own stories + delete)
# --------------------------------------------------------------------------- #

def test_creator_dashboard_lists_expired_stories_too(client, db_session):
    creator_headers = _make_creator(client)
    story = _upload_story(client, creator_headers)
    with db_session as db:
        _force_expire(db, story["id"])

    resp = client.get("/creator/stories", headers=creator_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1  # expired but still listed for the owner


def test_creator_dashboard_requires_creator_role(client):
    _register(client, "plain@example.com")
    headers = _login(client, "plain@example.com")
    assert client.get("/creator/stories", headers=headers).status_code == 403


def test_delete_own_story(client, db_session):
    creator_headers = _make_creator(client)
    story = _upload_story(client, creator_headers)
    assert client.get("/creator/stories", headers=creator_headers).status_code == 200

    resp = client.delete(f"/creator/stories/{story['id']}", headers=creator_headers)
    assert resp.status_code == 204
    assert client.get("/creator/stories", headers=creator_headers).json() == []
    with db_session as db:
        assert db.get(Story, story["id"]) is None


def test_cannot_delete_another_creators_story(client, db_session):
    creator_headers = _make_creator(client)
    story = _upload_story(client, creator_headers)
    _register(client, "creatortwo@example.com")
    other_headers = _login(client, "creatortwo@example.com")
    assert client.post("/creator/apply", headers=other_headers).status_code == 200

    resp = client.delete(f"/creator/stories/{story['id']}", headers=other_headers)
    assert resp.status_code == 404  # indistinguishable from missing


# --------------------------------------------------------------------------- #
# Expiry housekeeping (Celery sweep)
# --------------------------------------------------------------------------- #

def test_purge_expired_stories_removes_rows_and_originals(client, db_session):
    """The expiry sweep deletes expired stories + their storage originals
    while leaving live stories untouched (the Celery task delegates here)."""
    from app.media import get_original_storage
    from app.models import StoryMedia
    from app.services.stories import StoryService

    headers = _make_creator(client)
    live = _upload_story(client, headers, caption="Live one")
    expired = _upload_story(client, headers, caption="Expired one")
    with db_session as db:
        _force_expire(db, expired["id"])
        expired_media = db.scalar(
            select(StoryMedia.storage_key).where(StoryMedia.story_id == expired["id"])
        )
        live_media = db.scalar(
            select(StoryMedia.storage_key).where(StoryMedia.story_id == live["id"])
        )
        storage = get_original_storage()
        assert storage.exists(expired_media)
        assert storage.exists(live_media)

        count = StoryService(db).purge_expired()
        assert count == 1
        assert db.get(Story, expired["id"]) is None
        assert db.get(Story, live["id"]) is not None
        # Only the expired story's original is removed from storage.
        assert not storage.exists(expired_media)
        assert storage.exists(live_media)
