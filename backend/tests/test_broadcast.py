"""Tests for creator paid broadcasts (message + media with one-time unlock).

Acceptance: a paid broadcast goes to all subscribers as a **locked preview**
(metadata only — no media urls) until the subscriber pays the one-time unlock;
after the unlock they get full media access. The creator always has full
access. Unit tests cover the lock/unlock state machine (charge recorded,
idempotent repeat, failed charge grants nothing); integration tests cover the
full lock -> unlock -> full-access flow over the feed, media and unlock
endpoints.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image
from sqlalchemy import select

from app.models import BroadcastUnlock, Post, Subscription, SubscriptionStatus, User, UserRole
from app.payments.base import ChargeResult
from app.payments.mock import MockPaymentProvider
from app.services.broadcasts import (
    BroadcastNotPaidError,
    BroadcastService,
    PaymentFailedError,
)


def _real_jpeg(width: int = 320, height: int = 240) -> bytes:
    """A real decodable JPEG (served media is re-encoded, so it must decode)."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (60, 30, 220)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, email: str, password: str = "BcastCr123"):
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201


def _login(client, email: str, password: str = "BcastCr123") -> str:
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _make_creator(client, email: str = "cr@example.com") -> str:
    _register(client, email)
    token = _login(client, email)
    assert client.post("/creator/apply", headers=_bearer(token)).status_code == 200
    return token


def _upload_post(
    client,
    creator_token: str,
    *,
    caption: str = "Broadcast",
    price_cents: int | None = None,
) -> dict:
    data = {"caption": caption}
    if price_cents is not None:
        data["price_cents"] = str(price_cents)
    resp = client.post(
        "/posts",
        headers=_bearer(creator_token),
        data=data,
        files=[("files", ("photo.jpg", _real_jpeg(), "image/jpeg"))],
    )
    assert resp.status_code == 201
    return resp.json()


def _follow(
    db,
    subscriber: User,
    creator: User,
    *,
    status: SubscriptionStatus = SubscriptionStatus.active,
    days: int = 30,
) -> Subscription:
    sub = Subscription(
        subscriber_id=subscriber.id,
        creator_id=creator.id,
        status=status,
        current_period_start=datetime.now(timezone.utc) - timedelta(days=1),
        current_period_end=datetime.now(timezone.utc) + timedelta(days=days),
        payment_provider="mock",
        external_ref=f"sub_bcast_{subscriber.id}_{creator.id}",
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def _user_id(db, email: str) -> int:
    return db.scalar(select(User.id).where(User.email == email))


def _media_url(post: dict) -> str:
    return f"/content/{post['id']}/media?media_id={post['media'][0]['id']}"


def _make_fan_follower(client, db, email: str = "fan@example.com") -> str:
    _register(client, email)
    token = _login(client, email)
    with db:
        fan = db.get(User, _user_id(db, email))
        creator = db.get(User, _user_id(db, "cr@example.com"))
        _follow(db, fan, creator)
        fan_id = fan.id
    return token, fan_id


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


def _create_subscriber(db, email: str = "fan@example.com") -> User:
    user = User(
        email=email,
        username=email.split("@")[0],
        hashed_password="not-used-in-tests",
        role=UserRole.registered,
        is_active=True,
        is_creator=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _create_post(db, creator: User, *, price_cents: int | None = None) -> Post:
    post = Post(creator_id=creator.id, caption="Unit broadcast", broadcast_price_cents=price_cents)
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


# --------------------------------------------------------------------------- #
# Broadcast creation
# --------------------------------------------------------------------------- #

def test_creator_creates_paid_broadcast(client):
    token = _make_creator(client)
    post = _upload_post(client, token, caption="Pay to see", price_cents=500)

    assert post["broadcast_price_cents"] == 500
    assert post["unlocked"] is True  # the owner always has full access
    assert post["media"][0]["media_url"].startswith("/content/")


def test_regular_post_has_no_price(client):
    token = _make_creator(client)
    post = _upload_post(client, token, caption="Free post")
    assert post["broadcast_price_cents"] is None
    assert post["unlocked"] is None


def test_rejects_invalid_price(client):
    token = _make_creator(client)
    for bad in ("0", "-5"):
        resp = client.post(
            "/posts",
            headers=_bearer(token),
            data={"caption": "x", "price_cents": bad},
            files=[("files", ("photo.jpg", _real_jpeg(), "image/jpeg"))],
        )
        assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Locked preview (subscriber without payment)
# --------------------------------------------------------------------------- #

def test_subscriber_without_payment_sees_locked_preview(client, db_session):
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token, caption="Pay to see", price_cents=500)
    fan_token, _ = _make_fan_follower(client, db_session)

    # Feed: locked preview metadata only — no media urls, price shown.
    feed = client.get(f"/creators/{post['creator_id']}/posts", headers=_bearer(fan_token))
    assert feed.status_code == 200
    body = feed.json()
    assert body["teaser"] is False  # subscriber feed, not a teaser
    item = body["posts"][0]
    assert item["broadcast_price_cents"] == 500
    assert item["unlocked"] is False
    assert item["media"][0]["media_url"] is None

    # Media endpoint: full content is denied while locked.
    served = client.get(_media_url(post), headers=_bearer(fan_token))
    assert served.status_code == 403
    assert "unlock" in served.json()["detail"].lower()


def test_anonymous_media_on_broadcast_still_401(client, db_session):
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token, price_cents=500)
    assert client.get(_media_url(post)).status_code == 401


def test_locked_broadcast_preview_for_anon_feed(client, db_session):
    """Anonymous feed shows the broadcast as a teaser with price but no urls."""
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token, caption="Pay to see", price_cents=500)

    feed = client.get(f"/creators/{post['creator_id']}/posts")
    item = feed.json()["posts"][0]
    assert feed.json()["teaser"] is True
    assert item["broadcast_price_cents"] == 500
    assert item["unlocked"] is False
    assert item["media"][0]["media_url"] is None


# --------------------------------------------------------------------------- #
# Unlock flow
# --------------------------------------------------------------------------- #

def test_subscriber_unlocks_and_gets_full_access(client, db_session):
    original = _real_jpeg()
    creator_token = _make_creator(client)
    resp = client.post(
        "/posts",
        headers=_bearer(creator_token),
        data={"caption": "Pay to see", "price_cents": "500"},
        files=[("files", ("photo.jpg", original, "image/jpeg"))],
    )
    assert resp.status_code == 201
    post = resp.json()
    fan_token, fan_id = _make_fan_follower(client, db_session)

    # Locked before payment…
    assert client.get(_media_url(post), headers=_bearer(fan_token)).status_code == 403

    # …unlock with the one-time payment…
    unlock = client.post(f"/content/{post['id']}/unlock", headers=_bearer(fan_token))
    assert unlock.status_code == 201  # first unlock is a creation
    body = unlock.json()
    assert body["already_unlocked"] is False
    assert body["post_id"] == post["id"]
    assert body["broadcast_price_cents"] == 500
    assert body["unlock"]["subscriber_id"] == fan_id
    assert body["unlock"]["payment_provider"] == "mock"
    assert body["unlock"]["external_ref"].startswith("ch_mock_")

    # …full media access now, watermarked never the original.
    served = client.get(_media_url(post), headers=_bearer(fan_token))
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/jpeg")
    assert served.content != original

    # The feed now shows the broadcast unlocked with media urls.
    feed = client.get(f"/creators/{post['creator_id']}/posts", headers=_bearer(fan_token))
    item = feed.json()["posts"][0]
    assert item["unlocked"] is True
    assert item["media"][0]["media_url"].startswith("/content/")


def test_unlock_is_idempotent_returns_existing_row(client, db_session):
    """A repeat unlock returns the original row (201 then 200, one row total)."""
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token, price_cents=500)
    fan_token, _ = _make_fan_follower(client, db_session)

    first = client.post(f"/content/{post['id']}/unlock", headers=_bearer(fan_token))
    assert first.status_code == 201
    assert first.json()["already_unlocked"] is False

    second = client.post(f"/content/{post['id']}/unlock", headers=_bearer(fan_token))
    assert second.status_code == 200
    assert second.json()["already_unlocked"] is True
    assert second.json()["unlock"]["id"] == first.json()["unlock"]["id"]

    with db_session as db:
        row_ids = db.scalars(
            select(BroadcastUnlock.id).where(BroadcastUnlock.post_id == post["id"])
        ).all()
    assert row_ids == [first.json()["unlock"]["id"]]  # exactly one row, unchanged


def test_unlock_requires_active_subscription(client, db_session):
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token, price_cents=500)
    _register(client, "nonfan@example.com")
    nonfan_token = _login(client, "nonfan@example.com")
    assert (
        client.post(f"/content/{post['id']}/unlock", headers=_bearer(nonfan_token)).status_code
        == 403
    )


def test_unlock_requires_current_subscription_not_expired(client, db_session):
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token, price_cents=500)
    _register(client, "exfan@example.com")
    fan_token = _login(client, "exfan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "exfan@example.com"))
        creator = db.get(User, _user_id(db, "cr@example.com"))
        _follow(db, fan, creator, days=-5)  # period already over

    assert (
        client.post(f"/content/{post['id']}/unlock", headers=_bearer(fan_token)).status_code
        == 403
    )


def test_unlock_regular_post_is_rejected(client, db_session):
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token, caption="Free")
    fan_token, _ = _make_fan_follower(client, db_session)
    resp = client.post(f"/content/{post['id']}/unlock", headers=_bearer(fan_token))
    assert resp.status_code == 400


def test_unlock_unknown_post_404(client, db_session):
    creator_token = _make_creator(client)
    _upload_post(client, creator_token, price_cents=500)
    fan_token, _ = _make_fan_follower(client, db_session)
    assert client.post("/content/999999/unlock", headers=_bearer(fan_token)).status_code == 404


def test_creator_cannot_unlock_own_broadcast(client):
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token, price_cents=500)
    resp = client.post(f"/content/{post['id']}/unlock", headers=_bearer(creator_token))
    assert resp.status_code == 409


def test_creator_has_full_media_access_without_unlock(client):
    original = _real_jpeg()
    creator_token = _make_creator(client)
    resp = client.post(
        "/posts",
        headers=_bearer(creator_token),
        data={"caption": "Mine", "price_cents": "500"},
        files=[("files", ("photo.jpg", original, "image/jpeg"))],
    )
    post = resp.json()

    served = client.get(_media_url(post), headers=_bearer(creator_token))
    assert served.status_code == 200
    assert served.content != original


def test_feed_mixes_locked_and_unlocked_broadcasts(client, db_session):
    creator_token = _make_creator(client)
    _upload_post(client, creator_token, caption="Locked one", price_cents=500)
    free = _upload_post(client, creator_token, caption="Free one")
    paid = _upload_post(client, creator_token, caption="Will unlock", price_cents=700)
    fan_token, _ = _make_fan_follower(client, db_session)

    client.post(f"/content/{paid['id']}/unlock", headers=_bearer(fan_token))

    feed = client.get(f"/creators/{free['creator_id']}/posts", headers=_bearer(fan_token))
    items = {p["caption"]: p for p in feed.json()["posts"]}
    # Paid-but-unlocked broadcast: full media.
    assert items["Will unlock"]["unlocked"] is True
    assert items["Will unlock"]["media"][0]["media_url"] is not None
    # Paid-but-locked broadcast: preview only.
    assert items["Locked one"]["unlocked"] is False
    assert items["Locked one"]["media"][0]["media_url"] is None
    # Regular post: not a broadcast.
    assert items["Free one"]["broadcast_price_cents"] is None
    assert items["Free one"]["unlocked"] is None
    assert items["Free one"]["media"][0]["media_url"] is not None


# --------------------------------------------------------------------------- #
# Service unit tests (lock/unlock state machine)
# --------------------------------------------------------------------------- #

def test_service_unlock_records_charge_once(db_session):
    db = db_session
    creator = _create_creator(db)
    post = _create_post(db, creator, price_cents=500)
    subscriber = _create_subscriber(db)
    provider = MockPaymentProvider()
    service = BroadcastService(db, provider=provider)

    unlock, created = service.unlock(subscriber.id, post)
    assert created is True
    assert unlock.subscriber_id == subscriber.id
    assert unlock.post_id == post.id
    assert unlock.payment_provider == "mock"
    assert unlock.external_ref.startswith("ch_mock_")
    assert service.is_unlocked(subscriber.id, post.id) is True
    assert service.unlocked_post_ids(subscriber.id, [post.id]) == {post.id}

    # A repeat unlock never charges twice.
    again, created2 = service.unlock(subscriber.id, post)
    assert created2 is False
    assert again.id == unlock.id
    assert len(provider.charges) == 1


def test_service_failed_charge_grants_no_unlock(db_session):
    class _FailingProvider:
        name = "mock"

        def charge_one_time(self, request):
            return ChargeResult(
                external_ref="ch_failed_1",
                status="failed",
                amount_cents=request.amount_cents,
                currency=request.currency,
                raw={},
            )

    db = db_session
    creator = _create_creator(db)
    post = _create_post(db, creator, price_cents=500)
    subscriber = _create_subscriber(db)
    service = BroadcastService(db, provider=_FailingProvider())

    with pytest.raises(PaymentFailedError):
        service.unlock(subscriber.id, post)
    assert service.is_unlocked(subscriber.id, post.id) is False


def test_service_unlock_regular_post_raises(db_session):
    db = db_session
    creator = _create_creator(db)
    post = _create_post(db, creator, price_cents=None)
    subscriber = _create_subscriber(db)
    service = BroadcastService(db, provider=MockPaymentProvider())

    with pytest.raises(BroadcastNotPaidError):
        service.unlock(subscriber.id, post)
