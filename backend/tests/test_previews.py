"""Public blurred-preview tests.

``GET /preview/{post_id}/media?media_id={id}`` serves a blurred ``PREVIEW``
teaser of a post's media to **any** visitor (no auth) — the real bytes are
never exposed, and hidden posts / wrong media ids 404 exactly like the
authenticated endpoint. The feed wires the same urls: non-follower teasers and
locked paid broadcasts carry ``preview_url`` on each media item while
``media_url`` stays withheld.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO

from PIL import Image
from sqlalchemy import select

from app.models import Subscription, SubscriptionStatus, User


def _register(client, email: str, password: str = "PrevCr123"):
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201


def _login(client, email: str, password: str = "PrevCr123"):
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_creator(client, email: str = "prevcrs@example.com"):
    _register(client, email)
    headers = _login(client, email)
    assert client.post("/creator/apply", headers=headers).status_code == 200
    return headers


def _user_id(db, email: str) -> int:
    return db.scalar(select(User.id).where(User.email == email))


def _jpeg_bytes(color=(120, 80, 40)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (24, 16), color).save(buf, format="JPEG")
    return buf.getvalue()


def _create_post(client, creator_headers, *, price_cents=None) -> dict:
    files = {"files": ("p.jpg", _jpeg_bytes(), "image/jpeg")}
    data = {"caption": "hello previews"}
    if price_cents is not None:
        data["price_cents"] = str(price_cents)
    resp = client.post("/posts", headers=creator_headers, data=data, files=files)
    assert resp.status_code == 201
    return resp.json()


def _media_of(post: dict) -> dict:
    return post["media"][0]


def _follow(db, subscriber_id: int, creator_id: int) -> None:
    db.add(
        Subscription(
            subscriber_id=subscriber_id,
            creator_id=creator_id,
            status=SubscriptionStatus.active,
            current_period_start=datetime.now(timezone.utc) - timedelta(days=1),
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
            payment_provider="mock",
            external_ref=f"sub_prev_{subscriber_id}_{creator_id}",
        )
    )
    db.commit()


# --------------------------------------------------------------------------- #
# Public preview endpoint
# --------------------------------------------------------------------------- #


def test_preview_serves_blurred_bytes_to_anonymous(client):
    """No auth needed; the bytes are a re-encoded (blurred) transform, not the original."""
    creator_headers = _make_creator(client)
    post = _create_post(client, creator_headers)
    media = _media_of(post)
    original = _jpeg_bytes()

    resp = client.get(f"/preview/{post['id']}/media?media_id={media['id']}")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.content[:2] == b"\xff\xd8"  # re-encoded JPEG
    assert resp.content != original  # blurred + stamped — never the original


def test_preview_404_for_hidden_and_missing(client):
    creator_headers = _make_creator(client)
    post = _create_post(client, creator_headers)
    media = _media_of(post)

    assert client.get("/preview/999999/media?media_id=1").status_code == 404
    # Media id that doesn't belong to the post.
    other = _create_post(client, creator_headers)
    other_media = _media_of(other)
    assert (
        client.get(
            f"/preview/{post['id']}/media?media_id={other_media['id']}"
        ).status_code
        == 404
    )
    # A hidden post is indistinguishable from a missing one.
    client.patch(
        f"/creator/content/{post['id']}",
        headers=creator_headers,
        json={"is_visible": False},
    )
    assert (
        client.get(f"/preview/{post['id']}/media?media_id={media['id']}").status_code
        == 404
    )


# --------------------------------------------------------------------------- #
# Feed wiring: preview_url on teasers / locked broadcasts only
# --------------------------------------------------------------------------- #


def test_teaser_feed_carries_preview_urls_not_media_urls(client):
    creator_headers = _make_creator(client)
    post = _create_post(client, creator_headers)

    feed = client.get(f"/creators/{post['creator_id']}/posts").json()
    assert feed["teaser"] is True
    media = _media_of(feed["posts"][0])
    assert media["media_url"] is None
    assert media["preview_url"] == f"/preview/{post['id']}/media?media_id={media['id']}"


def test_follower_feed_has_media_urls_and_no_preview(client, db_session):
    creator_headers = _make_creator(client)
    post = _create_post(client, creator_headers)
    creator_id = post["creator_id"]

    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        _follow(db, _user_id(db, "fan@example.com"), creator_id)

    feed = client.get(f"/creators/{creator_id}/posts", headers=fan_headers).json()
    assert feed["teaser"] is False
    media = _media_of(feed["posts"][0])
    assert media["media_url"].startswith(f"/content/{post['id']}/media")
    assert media["preview_url"] is None


def test_locked_broadcast_preview_for_followers(client, db_session):
    """A follower who hasn't unlocked a paid broadcast gets a blurred preview."""
    creator_headers = _make_creator(client)
    post = _create_post(client, creator_headers, price_cents=500)
    creator_id = post["creator_id"]

    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        _follow(db, _user_id(db, "fan@example.com"), creator_id)

    feed = client.get(f"/creators/{creator_id}/posts", headers=fan_headers).json()
    item = feed["posts"][0]
    assert item["unlocked"] is False
    media = _media_of(item)
    assert media["media_url"] is None
    assert media["preview_url"] is not None
