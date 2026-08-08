"""Creator block/ban (user banning) integration tests.

Acceptance: a creator can block a misbehaving subscriber from the dashboard.
Blocking is a hard, immediate revocation for that (creator, user) pair — the
blocked user is demoted from ``follower`` (feed/media/stories/engagement all
resolve as registered), DMs to the creator are rejected, ``POST /subscribe``
returns 403, and any active subscription is canceled locally. Unblocking
restores the ability to re-subscribe (access returns on their next checkout).
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from PIL import Image
from sqlalchemy import select

from app.models import BlockedUser, Subscription, SubscriptionStatus, User

CREATOR_EMAIL = "blockcr@example.com"


def _real_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (70, 130, 90)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _register(client, email: str, password: str = "Block123"):
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201


def _login(client, email: str, password: str = "Block123"):
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_creator(client, email: str = CREATOR_EMAIL):
    _register(client, email)
    headers = _login(client, email)
    assert client.post("/creator/apply", headers=headers).status_code == 200
    return headers


def _enable_messaging(client, creator_headers):
    """Turn the creator's DM policy on (defaults off) so fans can start threads."""
    resp = client.put(
        "/creator/messaging-settings",
        headers=creator_headers,
        json={"allow_messages_from_all_followers": True},
    )
    assert resp.status_code == 200


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
        external_ref=f"sub_block_{subscriber.id}_{creator.id}",
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
# Block / unblock endpoints
# --------------------------------------------------------------------------- #

def test_block_requires_creator(client, db_session):
    assert client.post("/creator/blocked", json={"user_id": 1}).status_code == 401
    _make_creator(client)
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    # A registered (non-creator) user can't block.
    resp = client.post("/creator/blocked", json={"user_id": 1}, headers=fan_headers)
    assert resp.status_code == 403


def test_block_unknown_user_404(client):
    creator_headers = _make_creator(client)
    resp = client.post("/creator/blocked", json={"user_id": 999999}, headers=creator_headers)
    assert resp.status_code == 404


def test_block_self_400(client):
    creator_headers = _make_creator(client)
    resp = client.get("/creator/profile", headers=creator_headers)
    creator_id = resp.json()["user_id"]
    blocked = client.post(
        "/creator/blocked", json={"user_id": creator_id}, headers=creator_headers
    )
    assert blocked.status_code == 400


def test_block_creator_400(client):
    creator_headers = _make_creator(client)
    _register(client, "other@example.com")
    other_headers = _login(client, "other@example.com")
    assert client.post("/creator/apply", headers=other_headers).status_code == 200
    other_id = client.get("/creator/profile", headers=other_headers).json()["user_id"]
    resp = client.post("/creator/blocked", json={"user_id": other_id}, headers=creator_headers)
    assert resp.status_code == 400
    assert "subscribers" in resp.json()["detail"]


def test_block_and_list(client, db_session):
    creator_headers = _make_creator(client)
    fan_headers = _make_fan(client, db_session)
    fan_id = _user_id(db_session, "fan@example.com")

    resp = client.post(
        "/creator/blocked", json={"user_id": fan_id}, headers=creator_headers
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["user_id"] == fan_id
    assert body["email"] == "fan@example.com"
    assert body["was_subscriber"] is True
    assert body["subscription_status"] == "active"

    listing = client.get("/creator/blocked", headers=creator_headers)
    assert listing.status_code == 200
    data = listing.json()
    assert data["total"] == 1
    assert data["items"][0]["user_id"] == fan_id

    # The blocked user is gone from the Subscribers list (managed on Blocked).
    subs = client.get("/creator/subscribers", headers=creator_headers).json()
    assert subs["total"] == 0


def test_block_is_idempotent(client, db_session):
    creator_headers = _make_creator(client)
    _register(client, "fan@example.com")
    fan_id = _user_id(db_session, "fan@example.com")
    assert (
        client.post("/creator/blocked", json={"user_id": fan_id}, headers=creator_headers).status_code
        == 201
    )
    resp = client.post(
        "/creator/blocked", json={"user_id": fan_id}, headers=creator_headers
    )
    assert resp.status_code == 201
    assert resp.json()["user_id"] == fan_id
    assert client.get("/creator/blocked", headers=creator_headers).json()["total"] == 1


def test_unblock(client, db_session):
    creator_headers = _make_creator(client)
    _register(client, "fan@example.com")
    fan_id = _user_id(db_session, "fan@example.com")
    client.post("/creator/blocked", json={"user_id": fan_id}, headers=creator_headers)
    assert (
        client.delete(f"/creator/blocked/{fan_id}", headers=creator_headers).status_code == 204
    )
    assert client.get("/creator/blocked", headers=creator_headers).json()["total"] == 0
    # Unblocking an unknown block is a no-op 204.
    assert (
        client.delete(f"/creator/blocked/{fan_id}", headers=creator_headers).status_code == 204
    )


def test_list_requires_creator(client, db_session):
    assert client.get("/creator/blocked").status_code == 401
    _make_creator(client)
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    assert client.get("/creator/blocked", headers=fan_headers).status_code == 403


# --------------------------------------------------------------------------- #
# Access revocation while blocked
# --------------------------------------------------------------------------- #

def test_blocked_user_loses_follower_access(client, db_session):
    creator_headers = _make_creator(client)
    post = _upload_post(client, creator_headers)
    fan_headers = _make_fan(client, db_session)
    fan_id = _user_id(db_session, "fan@example.com")

    # Follower sees the full feed (real media urls).
    feed = client.get(f"/creators/{post['creator_id']}/posts", headers=fan_headers)
    assert feed.json()["teaser"] is False
    assert feed.json()["posts"][0]["media"][0]["media_url"] is not None

    # Block -> the feed becomes a teaser (no media urls).
    client.post("/creator/blocked", json={"user_id": fan_id}, headers=creator_headers)
    feed = client.get(f"/creators/{post['creator_id']}/posts", headers=fan_headers)
    assert feed.status_code == 200
    assert feed.json()["teaser"] is True
    assert feed.json()["posts"][0]["media"][0]["media_url"] is None

    # Media serving is refused.
    media_id = post["media"][0]["id"]
    resp = client.get(
        f"/content/{post['id']}/media?media_id={media_id}", headers=fan_headers
    )
    assert resp.status_code == 403

    # Stories are refused too.
    story_resp = client.get(f"/stories/{post['creator_id']}", headers=fan_headers)
    assert story_resp.status_code == 403

    # Engagement (like + comment) is refused.
    assert client.post(f"/posts/{post['id']}/like", headers=fan_headers).status_code == 403
    assert (
        client.post(
            f"/posts/{post['id']}/comments",
            headers=fan_headers,
            json={"body": "Hey"},
        ).status_code
        == 403
    )

    # The access resolver reports registered, not follower.
    status = client.get(
        f"/subscribe/status?creator_id={post['creator_id']}", headers=fan_headers
    )
    assert status.json()["viewer_level"] == "registered"


def test_blocked_user_cannot_message_creator(client, db_session):
    creator_headers = _make_creator(client)
    _enable_messaging(client, creator_headers)
    creator_id = client.get("/creator/profile", headers=creator_headers).json()["user_id"]
    fan_headers = _make_fan(client, db_session)
    fan_id = _user_id(db_session, "fan@example.com")

    # Before the block the fan can message (active follower).
    ok = client.post(
        "/messages",
        headers=fan_headers,
        json={"recipient_id": creator_id, "body": "Hi!"},
    )
    assert ok.status_code == 201

    # Block -> sending is rejected even with an existing thread.
    client.post("/creator/blocked", json={"user_id": fan_id}, headers=creator_headers)
    resp = client.post(
        "/messages",
        headers=fan_headers,
        json={"recipient_id": creator_id, "body": "Still here?"},
    )
    assert resp.status_code == 403
    assert "blocked" in resp.json()["detail"].lower()

    # The status endpoint reports the block as the reason.
    status = client.get(f"/messages/status?recipient_id={creator_id}", headers=fan_headers)
    assert status.status_code == 200
    assert status.json()["can_message"] is False
    assert "blocked" in status.json()["reason"].lower()


def test_blocked_user_cannot_subscribe(client, db_session):
    creator_headers = _make_creator(client)
    creator_id = client.get("/creator/profile", headers=creator_headers).json()["user_id"]
    # Give the creator a gateway so subscribe resolves past the gateway step
    # and hits the block gate (the 403 we assert).
    from app.models import CreatorGatewayConfig

    with db_session as db:
        db.add(CreatorGatewayConfig(creator_id=creator_id, gateway="mock", enabled=True, config={}))
        db.commit()
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    fan_id = _user_id(db_session, "fan@example.com")

    client.post("/creator/blocked", json={"user_id": fan_id}, headers=creator_headers)
    resp = client.post(
        "/subscribe",
        headers=fan_headers,
        json={
            "creator_id": creator_id,
            "accepted_tos": True,
            "age_confirmed": True,
        },
    )
    assert resp.status_code == 403
    assert "blocked" in resp.json()["detail"].lower()


def test_block_cancels_active_subscription(client, db_session):
    creator_headers = _make_creator(client)
    creator_id = client.get("/creator/profile", headers=creator_headers).json()["user_id"]
    _make_fan(client, db_session)
    fan_id = _user_id(db_session, "fan@example.com")

    client.post("/creator/blocked", json={"user_id": fan_id}, headers=creator_headers)

    with db_session as db:
        sub = db.scalar(
            select(Subscription).where(
                Subscription.subscriber_id == fan_id,
                Subscription.creator_id == creator_id,
            )
        )
        assert sub is not None
        assert sub.status == SubscriptionStatus.canceled
        assert sub.checkout_url is None
        assert sub.cancel_at_period_end is False
        # The blocked row exists.
        assert (
            db.scalar(
                select(BlockedUser.id).where(
                    BlockedUser.creator_id == sub.creator_id,
                    BlockedUser.user_id == fan_id,
                )
            )
            is not None
        )


def test_unblock_restores_access_after_resubscribe(client, db_session):
    creator_headers = _make_creator(client)
    creator_id = client.get("/creator/profile", headers=creator_headers).json()["user_id"]
    _make_fan(client, db_session)
    fan_id = _user_id(db_session, "fan@example.com")

    client.post("/creator/blocked", json={"user_id": fan_id}, headers=creator_headers)
    client.delete(f"/creator/blocked/{fan_id}", headers=creator_headers)

    # Unblocked — the user is no longer blocked in the DB.
    with db_session as db:
        assert (
            db.scalar(
                select(BlockedUser.id).where(
                    BlockedUser.creator_id == creator_id,
                    BlockedUser.user_id == fan_id,
                )
            )
            is None
        )
    # Blocking canceled the subscription; subscribing again (a new checkout)
    # is allowed now that the block is gone. The zero-config mock gateway is
    # enabled via DB so subscribe resolves a provider.
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        from app.models import CreatorGatewayConfig

        db.add(
            CreatorGatewayConfig(
                creator_id=creator_id, gateway="mock", enabled=True, config={}
            )
        )
        db.commit()
    resp = client.post(
        "/subscribe",
        headers=fan_headers,
        json={
            "creator_id": creator_id,
            "accepted_tos": True,
            "age_confirmed": True,
        },
    )
    assert resp.status_code == 201


def test_blocked_list_pagination(client, db_session):
    creator_headers = _make_creator(client)
    for i in range(3):
        _register(client, f"fan{i}@example.com")
        resp = client.post(
            "/creator/blocked",
            json={"user_id": _user_id(db_session, f"fan{i}@example.com")},
            headers=creator_headers,
        )
        assert resp.status_code == 201

    page1 = client.get(
        "/creator/blocked?page=1&page_size=2", headers=creator_headers
    ).json()
    assert page1["total"] == 3
    assert len(page1["items"]) == 2
    assert page1["has_more"] is True
    # Newest first (fan2 blocked last).
    assert page1["items"][0]["email"] == "fan2@example.com"

    page2 = client.get(
        "/creator/blocked?page=2&page_size=2", headers=creator_headers
    ).json()
    assert len(page2["items"]) == 1
    assert page2["has_more"] is False
