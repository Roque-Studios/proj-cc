"""Media gallery endpoint integration tests.

Acceptance: ``GET /creators/{creator_id}/media`` flattens every visible post's
media into one paginated gallery (newest post first). Gating mirrors the feed
exactly — anonymous/registered non-followers get blurred previews on everything
(``teaser: true``, real urls never leak), active followers/owners get real urls
except **locked paid broadcasts** (blurred preview + price + ``unlocked:
false``), hidden posts are excluded, and the creator's own paid content is
never locked for them.
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


def _make_creator(client, email: str = "galcr@example.com"):
    _register(client, email)
    headers = _login(client, email)
    assert client.post("/creator/apply", headers=headers).status_code == 200
    return headers


def _upload_post(client, headers, caption: str, *, price_cents: int | None = None) -> dict:
    """Upload a post (optionally a paid broadcast) via the API."""
    data = {"caption": caption}
    if price_cents is not None:
        data["price_cents"] = str(price_cents)
    resp = client.post(
        "/posts",
        headers=headers,
        data=data,
        files=[("files", (f"{caption}.jpg", _real_jpeg(), "image/jpeg"))],
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
        external_ref=f"sub_gal_{subscriber.id}_{creator.id}",
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

def test_gallery_404_for_unknown_creator(client):
    assert client.get("/creators/999999/media").status_code == 404


def test_gallery_404_when_id_is_not_a_creator(client, db_session):
    _register(client, "plain@example.com")
    with db_session as db:
        plain_id = _user_id(db, "plain@example.com")
    assert client.get(f"/creators/{plain_id}/media").status_code == 404


def test_anonymous_gets_everything_blurred(client):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers, "Secret photo")

    resp = client.get(f"/creators/{post['creator_id']}/media")
    assert resp.status_code == 200
    body = resp.json()
    assert body["teaser"] is True
    assert len(body["items"]) == 1
    item = body["items"][0]
    # The real url never leaks; the blurred preview carries the tile.
    assert item["media_url"] is None
    assert item["preview_url"].startswith("/preview/")


def test_registered_non_follower_gets_everything_blurred(client):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers, "Locked post")
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")

    resp = client.get(f"/creators/{post['creator_id']}/media", headers=fan_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["teaser"] is True
    assert body["items"][0]["media_url"] is None
    assert body["items"][0]["preview_url"].startswith("/preview/")


def test_follower_gets_real_urls(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers, "Full photo")
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "fan@example.com"))
        creator = db.get(User, _user_id(db, "galcr@example.com"))
        _follow(db, fan, creator)

    resp = client.get(f"/creators/{post['creator_id']}/media", headers=fan_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["teaser"] is False
    item = body["items"][0]
    # Free post: the auth-gated content url, no preview.
    assert item["media_url"].startswith("/content/")
    assert item["preview_url"] is None
    assert item["unlocked"] is None  # not a paid broadcast
    assert item["post_caption"] == "Full photo"


def test_follower_sees_locked_paid_broadcast_blurred(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers, "Pay me", price_cents=500)
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "fan@example.com"))
        creator = db.get(User, _user_id(db, "galcr@example.com"))
        _follow(db, fan, creator)

    resp = client.get(f"/creators/{post['creator_id']}/media", headers=fan_headers)
    body = resp.json()
    assert body["teaser"] is False
    item = body["items"][0]
    # Locked: real url withheld, blurred preview + price + locked state.
    assert item["media_url"] is None
    assert item["preview_url"].startswith("/preview/")
    assert item["broadcast_price_cents"] == 500
    assert item["unlocked"] is False


def test_owner_never_sees_own_paid_content_locked(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers, "Mine", price_cents=500)

    resp = client.get(
        f"/creators/{post['creator_id']}/media", headers=creator_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["teaser"] is False
    item = body["items"][0]
    # The owner always has full access: real url, unlocked, no preview.
    assert item["media_url"].startswith("/content/")
    assert item["preview_url"] is None
    assert item["unlocked"] is True


def test_gallery_flattens_all_posts_newest_first(client, db_session):
    creator_headers = _make_creator(client)
    # Two posts, the newest uploaded later — gallery must list newest first.
    first = _upload_post(client, creator_headers, "Old post")
    second = _upload_post(client, creator_headers, "New post")
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "fan@example.com"))
        creator = db.get(User, _user_id(db, "galcr@example.com"))
        _follow(db, fan, creator)

    resp = client.get(
        f"/creators/{first['creator_id']}/media", headers=fan_headers
    )
    body = resp.json()
    assert len(body["items"]) == 2
    # Newest post's media first (both single-media posts).
    assert body["items"][0]["post_id"] == second["id"]
    assert body["items"][1]["post_id"] == first["id"]


def test_gallery_flattens_multiple_media_per_post(client, db_session):
    """A multi-media post contributes one tile per file, in upload order."""
    creator_headers = _make_creator(client)
    # Two files in one post.
    resp = client.post(
        "/posts",
        headers=creator_headers,
        data={"caption": "Two shots"},
        files=[
            ("files", ("a.jpg", _real_jpeg(), "image/jpeg")),
            ("files", ("b.jpg", _real_jpeg(), "image/jpeg")),
        ],
    )
    assert resp.status_code == 201
    post = resp.json()
    assert len(post["media"]) == 2
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "fan@example.com"))
        creator = db.get(User, _user_id(db, "galcr@example.com"))
        _follow(db, fan, creator)

    resp = client.get(f"/creators/{post['creator_id']}/media", headers=fan_headers)
    body = resp.json()
    assert body["total"] == 2
    assert [i["media_id"] for i in body["items"]] == [
        m["id"] for m in post["media"]
    ]  # same post, media id order


def test_gallery_excludes_hidden_posts(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers, "Visible")
    hidden = _upload_post(client, creator_headers, "Hidden")
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "fan@example.com"))
        creator = db.get(User, _user_id(db, "galcr@example.com"))
        _follow(db, fan, creator)

    # Hide the newest post via the creator dashboard.
    assert client.patch(
        f"/creator/content/{hidden['id']}",
        headers=creator_headers,
        json={"is_visible": False},
    ).status_code == 200

    resp = client.get(f"/creators/{post['creator_id']}/media", headers=fan_headers)
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["post_id"] == post["id"]


# --------------------------------------------------------------------------- #
# Pagination
# --------------------------------------------------------------------------- #

def test_gallery_pagination(client, db_session):
    creator_headers = _make_creator(client)
    for caption in ("first", "second", "third"):
        _upload_post(client, creator_headers, caption)
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "fan@example.com"))
        creator = db.get(User, _user_id(db, "galcr@example.com"))
        _follow(db, fan, creator)
        creator_id = creator.id

    page1 = client.get(
        f"/creators/{creator_id}/media?page=1&page_size=2", headers=fan_headers
    )
    assert page1.status_code == 200
    body1 = page1.json()
    assert body1["total"] == 3
    assert len(body1["items"]) == 2
    assert body1["has_more"] is True
    assert [i["post_caption"] for i in body1["items"]] == ["third", "second"]

    page2 = client.get(
        f"/creators/{creator_id}/media?page=2&page_size=2", headers=fan_headers
    )
    body2 = page2.json()
    assert len(body2["items"]) == 1
    assert body2["items"][0]["post_caption"] == "first"
    assert body2["has_more"] is False

    page3 = client.get(
        f"/creators/{creator_id}/media?page=3&page_size=2", headers=fan_headers
    )
    body3 = page3.json()
    assert body3["items"] == []
    assert body3["total"] == 3


def test_gallery_validation_bad_pagination(client):
    assert client.get("/creators/1/media?page=0").status_code == 422
    assert client.get("/creators/1/media?page_size=100").status_code == 422
