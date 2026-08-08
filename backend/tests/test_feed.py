"""Feed endpoint integration tests.

Acceptance: the paginated feed of a creator's posts is accessible only to
active followers; anonymous and registered (non-follower) viewers get a
teaser-only response (captions + media counts, no media urls). Covers all
three access levels plus expired subscriptions and pagination.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from PIL import Image
from sqlalchemy import select

from app.models import Subscription, SubscriptionStatus, User


def _real_jpeg() -> bytes:
    """A real decodable JPEG (uploads are re-encoded on serve)."""
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (70, 130, 90)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _register(client, email: str, password: str = "FeedCr123"):
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201


def _login(client, email: str, password: str = "FeedCr123"):
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_creator(client, email: str = "feedcr@example.com"):
    _register(client, email)
    headers = _login(client, email)
    assert client.post("/creator/apply", headers=headers).status_code == 200
    return headers


def _upload_post(client, headers, caption: str) -> dict:
    """Upload a post via the API and return its body (carries creator_id)."""
    resp = client.post(
        "/posts",
        headers=headers,
        data={"caption": caption},
        files=[("files", ("photo.jpg", _real_jpeg(), "image/jpeg"))],
    )
    assert resp.status_code == 201
    return resp.json()


def _follow(db, subscriber: User, creator: User, *, days: int = 30) -> Subscription:
    sub = Subscription(
        subscriber_id=subscriber.id,
        creator_id=creator.id,
        status=SubscriptionStatus.active,
        current_period_start=datetime.now(timezone.utc) - timedelta(days=1),
        current_period_end=datetime.now(timezone.utc) + timedelta(days=days),
        payment_provider="mock",
        external_ref=f"sub_feed_{subscriber.id}_{creator.id}",
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def _user_id(db, email: str) -> int:
    return db.scalar(select(User.id).where(User.email == email))


# --------------------------------------------------------------------------- #
# Access levels
# --------------------------------------------------------------------------- #

def test_feed_404_for_unknown_creator(client):
    assert client.get("/creators/999999/posts").status_code == 404


def test_feed_404_when_id_is_not_a_creator(client, db_session):
    _register(client, "plain@example.com")
    with db_session as db:
        plain_id = _user_id(db, "plain@example.com")
    assert client.get(f"/creators/{plain_id}/posts").status_code == 404


def test_anonymous_gets_teaser(client):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers, "Secret photo")

    resp = client.get(f"/creators/{post['creator_id']}/posts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["teaser"] is True
    assert len(body["posts"]) == 1
    assert body["posts"][0]["caption"] == "Secret photo"
    assert body["posts"][0]["media"][0]["media_url"] is None


def test_registered_non_follower_gets_teaser(client):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers, "Locked post")
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")

    resp = client.get(f"/creators/{post['creator_id']}/posts", headers=fan_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["teaser"] is True
    assert body["posts"][0]["media"][0]["media_url"] is None


def test_expired_subscription_gets_teaser(client, db_session):
    """An expired subscription classifies as registered -> teaser, not follower."""
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers, "Gone")
    _register(client, "exfan@example.com")
    fan_headers = _login(client, "exfan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "exfan@example.com"))
        creator = db.get(User, _user_id(db, "feedcr@example.com"))
        # Active status but the period ended -> not a current follower.
        _follow(db, fan, creator, days=-5)

    resp = client.get(f"/creators/{post['creator_id']}/posts", headers=fan_headers)
    assert resp.status_code == 200
    assert resp.json()["teaser"] is True


def test_owner_feed_shows_own_paid_broadcasts_unlocked(client, db_session):
    """The creator viewing their own feed never sees their paid content locked."""
    creator_headers = _make_creator(client)
    resp = client.post(
        "/posts",
        headers=creator_headers,
        data={"caption": "Pay to see", "price_cents": "500"},
        files=[("files", ("photo.jpg", _real_jpeg(), "image/jpeg"))],
    )
    assert resp.status_code == 201
    post = resp.json()

    feed = client.get(
        f"/creators/{post['creator_id']}/posts", headers=creator_headers
    )
    assert feed.status_code == 200
    body = feed.json()
    assert body["teaser"] is False
    item = body["posts"][0]
    # The owner always has full access: unlocked, real media urls, no preview.
    assert item["unlocked"] is True
    assert item["media"][0]["media_url"].startswith("/content/")


def test_follower_gets_full_feed(client, db_session):
    creator_headers = _make_creator(client)
    first = _upload_post(client, creator_headers, "Post one")
    _upload_post(client, creator_headers, "Post two")
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "fan@example.com"))
        creator = db.get(User, _user_id(db, "feedcr@example.com"))
        _follow(db, fan, creator)

    resp = client.get(f"/creators/{first['creator_id']}/posts", headers=fan_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["teaser"] is False
    assert len(body["posts"]) == 2
    # Newest first.
    assert [p["caption"] for p in body["posts"]] == ["Post two", "Post one"]
    # Media urls are included for followers (auth-gated content endpoint).
    assert body["posts"][1]["media"][0]["media_url"].startswith("/content/")
    assert first["media"][0]["media_url"] in [
        m["media_url"] for m in body["posts"][1]["media"]
    ]


# --------------------------------------------------------------------------- #
# Pagination
# --------------------------------------------------------------------------- #

def test_feed_pagination(client, db_session):
    creator_headers = _make_creator(client)
    for caption in ("first", "second", "third"):
        _upload_post(client, creator_headers, caption)
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "fan@example.com"))
        creator = db.get(User, _user_id(db, "feedcr@example.com"))
        _follow(db, fan, creator)
        creator_id = creator.id

    page1 = client.get(
        f"/creators/{creator_id}/posts?page=1&page_size=2", headers=fan_headers
    )
    assert page1.status_code == 200
    body1 = page1.json()
    assert body1["total"] == 3
    assert len(body1["posts"]) == 2
    assert body1["has_more"] is True
    assert [p["caption"] for p in body1["posts"]] == ["third", "second"]

    page2 = client.get(
        f"/creators/{creator_id}/posts?page=2&page_size=2", headers=fan_headers
    )
    body2 = page2.json()
    assert len(body2["posts"]) == 1
    assert body2["posts"][0]["caption"] == "first"
    assert body2["has_more"] is False

    # Out-of-range page: empty, still reports the real total.
    page3 = client.get(
        f"/creators/{creator_id}/posts?page=3&page_size=2", headers=fan_headers
    )
    body3 = page3.json()
    assert body3["posts"] == []
    assert body3["total"] == 3


def test_feed_validation_bad_pagination(client):
    assert client.get("/creators/1/posts?page=0").status_code == 422
    assert client.get("/creators/1/posts?page_size=100").status_code == 422
