"""Tests for the creator content-management dashboard endpoints.

Acceptance: a creator can list all their posts/broadcasts, edit captions,
delete posts, toggle visibility, and see engagement stats (views + unlock
count per paid post); non-creator roles cannot access the dashboard at all.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from PIL import Image
from sqlalchemy import select

from app.models import (
    PaidUnlock,
    Post,
    PostMedia,
    Subscription,
    SubscriptionStatus,
    User,
    UserRole,
)
from app.storage import get_original_storage

NOW = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)


def _real_jpeg(width: int = 320, height: int = 240) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (80, 140, 210)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, email: str, password: str = "DashCr123"):
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201


def _login(client, email: str, password: str = "DashCr123") -> str:
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _make_creator(client, email: str = "cr@example.com") -> str:
    """Register + apply as a creator; returns the access token."""
    _register(client, email)
    token = _login(client, email)
    assert client.post("/creator/apply", headers=_bearer(token)).status_code == 200
    return token


def _upload_post(
    client,
    creator_token: str,
    *,
    caption: str = "Post",
    price_cents: int | None = None,
    files: int = 1,
) -> dict:
    data = {"caption": caption}
    if price_cents is not None:
        data["price_cents"] = str(price_cents)
    resp = client.post(
        "/posts",
        headers=_bearer(creator_token),
        data=data,
        files=[
            ("files", (f"photo{i}.jpg", _real_jpeg(), "image/jpeg"))
            for i in range(files)
        ],
    )
    assert resp.status_code == 201
    return resp.json()


def _follow(
    db,
    subscriber: User,
    creator: User,
    *,
    status: SubscriptionStatus = SubscriptionStatus.active,
) -> Subscription:
    sub = Subscription(
        subscriber_id=subscriber.id,
        creator_id=creator.id,
        status=status,
        current_period_start=NOW - timedelta(days=1),
        current_period_end=NOW + timedelta(days=30),
        payment_provider="mock",
        external_ref=f"sub_dash_{subscriber.id}_{creator.id}",
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def _user_id(db, email: str) -> int:
    return db.scalar(select(User.id).where(User.email == email))


def _make_follower(client, db, email: str = "fan@example.com") -> tuple[str, int]:
    """Register a fan and subscribe them to cr@example.com; returns (token, id)."""
    _register(client, email)
    token = _login(client, email)
    with db:
        fan = db.get(User, _user_id(db, email))
        creator = db.get(User, _user_id(db, "cr@example.com"))
        _follow(db, fan, creator)
        fan_id = fan.id
    return token, fan_id


def _media_url(post: dict) -> str:
    return f"/content/{post['id']}/media?media_id={post['media'][0]['id']}"


def _add_unlock(db, subscriber_id: int, post_id: int, *, refunded: bool = False) -> PaidUnlock:
    row = PaidUnlock(
        subscriber_id=subscriber_id,
        post_id=post_id,
        payment_provider="mock",
        external_ref=f"ch_dash_{subscriber_id}_{post_id}",
        refunded_at=NOW if refunded else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --------------------------------------------------------------------------- #
# Role access (acceptance: non-creator roles cannot access this view)
# --------------------------------------------------------------------------- #


def test_dashboard_requires_auth(client):
    assert client.get("/creator/content").status_code == 401


def test_registered_user_cannot_access_dashboard(client, db_session):
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token)
    _register(client, "fan@example.com")
    fan_token = _login(client, "fan@example.com")

    assert client.get("/creator/content", headers=_bearer(fan_token)).status_code == 403
    resp = client.patch(
        f"/creator/content/{post['id']}",
        headers=_bearer(fan_token),
        json={"caption": "hax"},
    )
    assert resp.status_code == 403
    assert (
        client.delete(f"/creator/content/{post['id']}", headers=_bearer(fan_token)).status_code
        == 403
    )


# --------------------------------------------------------------------------- #
# Listing + engagement stats
# --------------------------------------------------------------------------- #


def test_list_shows_own_posts_newest_first_with_stats(client, db_session):
    token = _make_creator(client)
    _upload_post(client, token, caption="First")
    _upload_post(client, token, caption="Second", price_cents=500)
    _upload_post(client, token, caption="Third", files=2)

    resp = client.get("/creator/content", headers=_bearer(token))
    assert resp.status_code == 200
    posts = resp.json()
    assert [p["caption"] for p in posts] == ["Third", "Second", "First"]
    for p in posts:
        assert p["is_visible"] is True
        assert p["view_count"] == 0
        assert p["unlock_count"] == 0
        # Media urls always present for the owner (dashboard thumbnails).
        assert p["media"][0]["media_url"].startswith("/content/")
    by_caption = {p["caption"]: p for p in posts}
    assert by_caption["First"]["media_count"] == 1
    assert by_caption["Third"]["media_count"] == 2
    assert by_caption["Second"]["broadcast_price_cents"] == 500


def test_list_only_shows_own_posts(client, db_session):
    token_a = _make_creator(client, "a@example.com")
    token_b = _make_creator(client, "b@example.com")
    _upload_post(client, token_a, caption="A's post")
    _upload_post(client, token_b, caption="B's post")

    posts_a = client.get("/creator/content", headers=_bearer(token_a)).json()
    posts_b = client.get("/creator/content", headers=_bearer(token_b)).json()
    assert [p["caption"] for p in posts_a] == ["A's post"]
    assert [p["caption"] for p in posts_b] == ["B's post"]


def test_unlock_count_per_paid_post(client, db_session):
    """Acceptance: unlock count per paid post — refunded unlocks don't count."""
    token = _make_creator(client)
    post = _upload_post(client, token, price_cents=500)
    post_id = post["id"]

    _register(client, "fan1@example.com")
    _register(client, "fan2@example.com")
    with db_session as db:
        fan1 = db.get(User, _user_id(db, "fan1@example.com"))
        fan2 = db.get(User, _user_id(db, "fan2@example.com"))
        _add_unlock(db, fan1.id, post_id)
        _add_unlock(db, fan2.id, post_id, refunded=True)  # refunded -> excluded

    body = client.get("/creator/content", headers=_bearer(token)).json()
    assert body[0]["unlock_count"] == 1

    # A refund of the active one drops the count to zero.
    with db_session as db:
        row = db.scalar(
            select(PaidUnlock).where(
                PaidUnlock.post_id == post_id,
                PaidUnlock.refunded_at.is_(None),
            )
        )
        row.refunded_at = NOW
        db.commit()
    body = client.get("/creator/content", headers=_bearer(token)).json()
    assert body[0]["unlock_count"] == 0


def test_unlock_count_through_real_unlock_endpoint(client, db_session):
    """End-to-end: a subscriber's one-time unlock shows up in the dashboard."""
    token = _make_creator(client)
    post = _upload_post(client, token, price_cents=500)
    fan_token, _ = _make_follower(client, db_session)

    assert (
        client.post(f"/content/{post['id']}/unlock", headers=_bearer(fan_token)).status_code
        == 201
    )
    body = client.get("/creator/content", headers=_bearer(token)).json()
    assert body[0]["unlock_count"] == 1


def test_views_counted_on_media_served(client, db_session):
    """Media views served to non-owners count; owner + HEAD requests don't."""
    token = _make_creator(client)
    post = _upload_post(client, token)
    fan_token, _ = _make_follower(client, db_session)

    # Owner previews don't count.
    assert client.get(_media_url(post), headers=_bearer(token)).status_code == 200
    # HEAD probes don't count.
    assert client.head(_media_url(post), headers=_bearer(fan_token)).status_code == 200
    with db_session as db:
        assert db.get(Post, post["id"]).view_count == 0

    # Each GET by a follower counts — including watermark-cache hits.
    assert client.get(_media_url(post), headers=_bearer(fan_token)).status_code == 200
    assert client.get(_media_url(post), headers=_bearer(fan_token)).status_code == 200
    with db_session as db:
        assert db.get(Post, post["id"]).view_count == 2

    # Unauthorized attempts never count (locked broadcast, non-follower).
    paid = _upload_post(client, token, price_cents=500)
    assert client.get(_media_url(paid), headers=_bearer(fan_token)).status_code == 403
    with db_session as db:
        assert db.get(Post, paid["id"]).view_count == 0


# --------------------------------------------------------------------------- #
# Edit caption
# --------------------------------------------------------------------------- #


def test_edit_caption(client, db_session):
    token = _make_creator(client)
    post = _upload_post(client, token, caption="Before")

    resp = client.patch(
        f"/creator/content/{post['id']}",
        headers=_bearer(token),
        json={"caption": "After edit"},
    )
    assert resp.status_code == 200
    assert resp.json()["caption"] == "After edit"
    with db_session as db:
        assert db.get(Post, post["id"]).caption == "After edit"

    # Whitespace-only clears the caption; null clears it too.
    resp = client.patch(
        f"/creator/content/{post['id']}",
        headers=_bearer(token),
        json={"caption": "   "},
    )
    assert resp.json()["caption"] is None
    resp = client.patch(
        f"/creator/content/{post['id']}",
        headers=_bearer(token),
        json={"caption": None},
    )
    assert resp.json()["caption"] is None


def test_patch_applies_only_provided_fields(client, db_session):
    token = _make_creator(client)
    post = _upload_post(client, token, caption="Keep me")

    resp = client.patch(
        f"/creator/content/{post['id']}",
        headers=_bearer(token),
        json={"is_visible": False},
    )
    assert resp.status_code == 200
    assert resp.json()["is_visible"] is False
    assert resp.json()["caption"] == "Keep me"


def test_cannot_edit_another_creators_post(client, db_session):
    token_a = _make_creator(client, "a@example.com")
    token_b = _make_creator(client, "b@example.com")
    post = _upload_post(client, token_a)

    # Other creators get the same 404 as a missing post (no probing).
    resp = client.patch(
        f"/creator/content/{post['id']}",
        headers=_bearer(token_b),
        json={"caption": "hijack"},
    )
    assert resp.status_code == 404
    assert (
        client.delete(f"/creator/content/{post['id']}", headers=_bearer(token_b)).status_code
        == 404
    )


# --------------------------------------------------------------------------- #
# Visibility toggle (soft-archive)
# --------------------------------------------------------------------------- #


def test_visibility_toggle_hides_from_feed_and_media(client, db_session):
    token = _make_creator(client)
    post = _upload_post(client, token, caption="Secret post")
    post_id = post["id"]
    creator_id = post["creator_id"]
    fan_token, _ = _make_follower(client, db_session)

    # Visible: in the feed, media served to followers.
    feed = client.get(f"/creators/{creator_id}/posts", headers=_bearer(fan_token)).json()
    assert [p["caption"] for p in feed["posts"]] == ["Secret post"]
    assert client.get(_media_url(post), headers=_bearer(fan_token)).status_code == 200

    # Hide it.
    resp = client.patch(
        f"/creator/content/{post_id}",
        headers=_bearer(token),
        json={"is_visible": False},
    )
    assert resp.status_code == 200
    assert resp.json()["is_visible"] is False

    # The follower feed excludes it; media is 404 (not 403) for outsiders;
    # it can't be unlocked either.
    feed = client.get(f"/creators/{creator_id}/posts", headers=_bearer(fan_token)).json()
    assert feed["posts"] == []
    assert client.get(_media_url(post), headers=_bearer(fan_token)).status_code == 404
    assert (
        client.post(f"/content/{post_id}/unlock", headers=_bearer(fan_token)).status_code
        == 404
    )
    # Anonymous probes get the same 404 as a nonexistent post — a hidden post
    # is indistinguishable from a missing one to outsiders.
    assert client.get(_media_url(post)).status_code == 404

    # The owner still has full access through the dashboard + media endpoint.
    assert client.get(_media_url(post), headers=_bearer(token)).status_code == 200
    body = client.get("/creator/content", headers=_bearer(token)).json()
    assert [p["caption"] for p in body] == ["Secret post"]  # still listed (hidden)

    # Un-hide restores it to the feed.
    client.patch(f"/creator/content/{post_id}", headers=_bearer(token), json={"is_visible": True})
    feed = client.get(f"/creators/{creator_id}/posts", headers=_bearer(fan_token)).json()
    assert [p["caption"] for p in feed["posts"]] == ["Secret post"]


# --------------------------------------------------------------------------- #
# Delete
# --------------------------------------------------------------------------- #


def test_delete_post_removes_rows_and_originals(client, db_session):
    token = _make_creator(client)
    post = _upload_post(client, token, caption="Doomed", price_cents=300)
    post_id = post["id"]
    creator_id = post["creator_id"]
    fan_token, fan_id = _make_follower(client, db_session)
    _register(client, "fan2@example.com")
    with db_session as db:
        fan2 = db.get(User, _user_id(db, "fan2@example.com"))
        _add_unlock(db, fan_id, post_id)
        _add_unlock(db, fan2.id, post_id)
        media_rows = db.scalars(
            select(PostMedia).where(PostMedia.post_id == post_id)
        ).all()
        keys = [row.storage_key for row in media_rows]
        assert all(get_original_storage().exists(k) for k in keys)

    resp = client.delete(f"/creator/content/{post_id}", headers=_bearer(token))
    assert resp.status_code == 204

    # Rows gone, originals gone from the private store, feed empty, media 404.
    with db_session as db:
        assert db.get(Post, post_id) is None
        assert db.scalar(select(PostMedia).where(PostMedia.post_id == post_id)) is None
        assert (
            db.scalar(select(PaidUnlock).where(PaidUnlock.post_id == post_id)) is None
        )
        assert all(not get_original_storage().exists(k) for k in keys)

    assert client.get(f"/creators/{creator_id}/posts", headers=_bearer(fan_token)).json()[
        "posts"
    ] == []
    assert client.get(_media_url(post), headers=_bearer(fan_token)).status_code == 404
    assert client.get("/creator/content", headers=_bearer(token)).json() == []
