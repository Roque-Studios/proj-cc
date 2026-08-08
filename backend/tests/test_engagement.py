"""Post engagement (likes + comments) integration tests.

Acceptance: subscribers can like and comment on a creator's posts, and the
counts appear on the feed (and the creator's dashboard). Both actions follow
the same content gate as the media — creator + active followers only; unknown
or hidden posts 404. Liking is idempotent, comments are text-only (1..500
chars), and deletion is limited to the comment's author or the post's creator.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from PIL import Image
from sqlalchemy import select

from app.models import Subscription, SubscriptionStatus, User

CREATOR_EMAIL = "engcr@example.com"


def _real_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (70, 130, 90)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _register(client, email: str, password: str = "Engage123"):
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201


def _login(client, email: str, password: str = "Engage123"):
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_creator(client, email: str = CREATOR_EMAIL):
    _register(client, email)
    headers = _login(client, email)
    assert client.post("/creator/apply", headers=headers).status_code == 200
    return headers


def _upload_post(client, headers, caption: str = "Hello followers") -> dict:
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
        external_ref=f"sub_engage_{subscriber.id}_{creator.id}",
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def _user_id(db, email: str) -> int:
    return db.scalar(select(User.id).where(User.email == email))


def _make_fan(
    client,
    db_session,
    *,
    email: str = "fan@example.com",
    creator_email: str = CREATOR_EMAIL,
):
    """Register + subscribe ``email`` to ``creator_email``; return headers."""
    _register(client, email)
    fan_headers = _login(client, email)
    with db_session as db:
        fan = db.get(User, _user_id(db, email))
        creator = db.get(User, _user_id(db, creator_email))
        _follow(db, fan, creator)
    return fan_headers


# --------------------------------------------------------------------------- #
# Likes
# --------------------------------------------------------------------------- #

def test_like_requires_auth(client):
    assert client.post("/posts/1/like").status_code == 401


def test_like_unknown_post_404(client, db_session):
    # Auth runs before the lookup: anonymous gets 401, an authenticated
    # follower probing an unknown post gets 404 (no id enumeration).
    assert client.post("/posts/999999/like").status_code == 401
    creator_headers = _make_creator(client)
    _upload_post(client, creator_headers)
    fan_headers = _make_fan(client, db_session)
    assert client.post("/posts/999999/like", headers=fan_headers).status_code == 404


def test_non_follower_cannot_like(client):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    _register(client, "stranger@example.com")
    stranger_headers = _login(client, "stranger@example.com")

    resp = client.post(f"/posts/{post['id']}/like", headers=stranger_headers)
    assert resp.status_code == 403


def test_follower_likes_post(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    fan_headers = _make_fan(client, db_session)

    resp = client.post(f"/posts/{post['id']}/like", headers=fan_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["liked"] is True
    assert body["like_count"] == 1

    # The feed reports the like for this viewer.
    feed = client.get(f"/creators/{post['creator_id']}/posts", headers=fan_headers)
    item = feed.json()["posts"][0]
    assert item["like_count"] == 1
    assert item["liked_by_me"] is True


def test_like_is_idempotent(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    fan_headers = _make_fan(client, db_session)
    assert client.post(f"/posts/{post['id']}/like", headers=fan_headers).status_code == 200
    resp = client.post(f"/posts/{post['id']}/like", headers=fan_headers)
    assert resp.status_code == 200
    assert resp.json()["like_count"] == 1


def test_unlike_removes_like(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    fan_headers = _make_fan(client, db_session)
    client.post(f"/posts/{post['id']}/like", headers=fan_headers)
    resp = client.delete(f"/posts/{post['id']}/like", headers=fan_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["liked"] is False
    assert body["like_count"] == 0

    feed = client.get(f"/creators/{post['creator_id']}/posts", headers=fan_headers)
    item = feed.json()["posts"][0]
    assert item["like_count"] == 0
    assert item["liked_by_me"] is False


def test_unlike_idempotent(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    fan_headers = _make_fan(client, db_session)
    resp = client.delete(f"/posts/{post['id']}/like", headers=fan_headers)
    assert resp.status_code == 200
    assert resp.json()["liked"] is False
    assert resp.json()["like_count"] == 0


def test_like_hidden_post_404(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    patch = client.patch(
        f"/creator/content/{post['id']}",
        headers=creator_headers,
        json={"is_visible": False},
    )
    assert patch.status_code == 200
    fan_headers = _make_fan(client, db_session)
    assert client.post(f"/posts/{post['id']}/like", headers=fan_headers).status_code == 404


def test_owner_can_like_own_post(client):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    resp = client.post(f"/posts/{post['id']}/like", headers=creator_headers)
    assert resp.status_code == 200
    assert resp.json()["liked"] is True


# --------------------------------------------------------------------------- #
# Comments
# --------------------------------------------------------------------------- #

def test_comment_requires_auth(client):
    assert client.post("/posts/1/comments", json={"body": "Nice!"}).status_code == 401


def test_non_follower_cannot_comment(client):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    _register(client, "stranger@example.com")
    stranger_headers = _login(client, "stranger@example.com")
    resp = client.post(
        f"/posts/{post['id']}/comments",
        headers=stranger_headers,
        json={"body": "Nice!"},
    )
    assert resp.status_code == 403


def test_follower_comments_on_post(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    fan_headers = _make_fan(client, db_session)
    resp = client.post(
        f"/posts/{post['id']}/comments",
        headers=fan_headers,
        json={"body": "Amazing shot 🔥"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["body"] == "Amazing shot 🔥"
    assert body["post_id"] == post["id"]
    assert body["author_username"] == "fan"
    assert body["author_display_name"] is None
    assert body["author_avatar_url"] is None
    assert body["author_is_creator"] is False


def test_comment_blank_rejected(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    fan_headers = _make_fan(client, db_session)
    resp = client.post(
        f"/posts/{post['id']}/comments",
        headers=fan_headers,
        json={"body": "   "},
    )
    assert resp.status_code == 422


def test_comment_too_long_rejected(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    fan_headers = _make_fan(client, db_session)
    resp = client.post(
        f"/posts/{post['id']}/comments",
        headers=fan_headers,
        json={"body": "x" * 501},
    )
    assert resp.status_code == 422


def test_comments_list_paginated_newest_first(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    fan_headers = _make_fan(client, db_session)
    for text in ("first", "second", "third"):
        resp = client.post(
            f"/posts/{post['id']}/comments",
            headers=fan_headers,
            json={"body": text},
        )
        assert resp.status_code == 201

    page1 = client.get(
        f"/posts/{post['id']}/comments?page=1&page_size=2",
        headers=fan_headers,
    )
    assert page1.status_code == 200
    body = page1.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["has_more"] is True
    assert [c["body"] for c in body["items"]] == ["third", "second"]

    page2 = client.get(
        f"/posts/{post['id']}/comments?page=2&page_size=2",
        headers=fan_headers,
    )
    body2 = page2.json()
    assert len(body2["items"]) == 1
    assert body2["items"][0]["body"] == "first"
    assert body2["has_more"] is False


def test_comments_list_gated(client):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    assert client.get(f"/posts/{post['id']}/comments").status_code == 401
    _register(client, "stranger@example.com")
    stranger_headers = _login(client, "stranger@example.com")
    assert (
        client.get(f"/posts/{post['id']}/comments", headers=stranger_headers).status_code
        == 403
    )


def test_delete_own_comment(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    fan_headers = _make_fan(client, db_session)
    created = client.post(
        f"/posts/{post['id']}/comments",
        headers=fan_headers,
        json={"body": "Bye"},
    ).json()
    resp = client.delete(
        f"/posts/{post['id']}/comments/{created['id']}",
        headers=fan_headers,
    )
    assert resp.status_code == 204
    listing = client.get(f"/posts/{post['id']}/comments", headers=fan_headers).json()
    assert listing["total"] == 0


def test_subscriber_cannot_delete_another_comment(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    fan_headers = _make_fan(client, db_session)
    created = client.post(
        f"/posts/{post['id']}/comments",
        headers=fan_headers,
        json={"body": "Mine"},
    ).json()
    other_headers = _make_fan(
        client, db_session, email="otherfan@example.com", creator_email=CREATOR_EMAIL
    )
    resp = client.delete(
        f"/posts/{post['id']}/comments/{created['id']}",
        headers=other_headers,
    )
    assert resp.status_code == 403


def test_creator_can_delete_any_comment(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    fan_headers = _make_fan(client, db_session)
    created = client.post(
        f"/posts/{post['id']}/comments",
        headers=fan_headers,
        json={"body": "Moderated"},
    ).json()
    resp = client.delete(
        f"/posts/{post['id']}/comments/{created['id']}",
        headers=creator_headers,
    )
    assert resp.status_code == 204


def test_delete_missing_comment_404(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    fan_headers = _make_fan(client, db_session)
    resp = client.delete(f"/posts/{post['id']}/comments/999999", headers=fan_headers)
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Feed + dashboard engagement fields
# --------------------------------------------------------------------------- #

def test_feed_carries_engagement_counts_for_anonymous_teaser(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    fan_headers = _make_fan(client, db_session)
    client.post(f"/posts/{post['id']}/like", headers=fan_headers)
    client.post(
        f"/posts/{post['id']}/comments",
        headers=fan_headers,
        json={"body": "Wow"},
    )

    # Anonymous teaser still reports the public counts (no like state).
    teaser = client.get(f"/creators/{post['creator_id']}/posts")
    item = teaser.json()["posts"][0]
    assert item["like_count"] == 1
    assert item["comment_count"] == 1
    assert item["liked_by_me"] is False


def test_dashboard_carries_like_and_comment_counts(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    fan_headers = _make_fan(client, db_session)
    client.post(f"/posts/{post['id']}/like", headers=fan_headers)
    client.post(
        f"/posts/{post['id']}/comments",
        headers=fan_headers,
        json={"body": "Nice"},
    )

    dash = client.get("/creator/content", headers=creator_headers)
    assert dash.status_code == 200
    item = dash.json()[0]
    assert item["like_count"] == 1
    assert item["comment_count"] == 1


def test_post_delete_removes_likes_and_comments(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    fan_headers = _make_fan(client, db_session)
    client.post(f"/posts/{post['id']}/like", headers=fan_headers)
    client.post(
        f"/posts/{post['id']}/comments",
        headers=fan_headers,
        json={"body": "Bye"},
    )

    assert (
        client.delete(f"/creator/content/{post['id']}", headers=creator_headers).status_code
        == 204
    )
    assert client.post(f"/posts/{post['id']}/like", headers=fan_headers).status_code == 404
    listing = client.get(f"/posts/{post['id']}/comments", headers=fan_headers)
    assert listing.status_code == 404
