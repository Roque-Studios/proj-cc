"""Tests for cancel / non-renew subscription flow.

Acceptance: cancelling marks ``cancel_at_period_end=true`` immediately and
access persists until the period ends; the scheduled expiry sweep (driven by
Celery beat in prod) flips it to ``canceled`` at period end. Covered with a
mock/injected clock (time-travel) plus endpoint + integration-style tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Subscription, SubscriptionStatus, User, UserRole
from app.payments.mock import MockPaymentProvider
from app.services.subscriptions import SubscriptionService

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _register(client, email: str, password: str = "Passw0rd1") -> dict:
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201
    token = client.post(
        "/auth/login", json={"email": email, "password": password}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _creator(db, email: str = "creator@example.com") -> User:
    creator = User(
        email=email,
        username=email.split("@")[0],
        hashed_password="x",
        role=UserRole.creator,
        is_active=True,
    )
    db.add(creator)
    db.commit()
    db.refresh(creator)
    return creator


def _active_subscription(
    db, subscriber_id: int, creator_id: int, period_end: datetime, provider=None
) -> Subscription:
    """Create an *active* subscription directly (already paid)."""
    sub = Subscription(
        subscriber_id=subscriber_id,
        creator_id=creator_id,
        status=SubscriptionStatus.active,
        current_period_start=period_end - timedelta(days=30),
        current_period_end=period_end,
        payment_provider="mock",
        external_ref="sub_mock_1",
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


# --------------------------------------------------------------------------- #
# Service-level: cancel_at_period_end
# --------------------------------------------------------------------------- #

def test_cancel_at_period_end_marks_flag_and_keeps_active(db_session):
    with db_session as db:
        subscriber = User(
            email="sub@example.com", username="sub", hashed_password="x",
            role=UserRole.registered, is_active=True,
        )
        creator = _creator(db, "cr@example.com")
        db.add(subscriber)
        db.commit()
        db.refresh(subscriber)
        sub = _active_subscription(db, subscriber.id, creator.id, NOW + timedelta(days=10))

        service = SubscriptionService(db, provider=MockPaymentProvider())
        result = service.cancel_at_period_end(sub)
        db.refresh(result)

        assert result.cancel_at_period_end is True
        assert result.status == SubscriptionStatus.active  # access persists


def test_expire_sweep_keeps_active_before_period_end(db_session):
    """Time-travel: before period end, a canceled-at-end sub stays active."""
    with db_session as db:
        subscriber = User(
            email="sub@example.com", username="sub", hashed_password="x",
            role=UserRole.registered, is_active=True,
        )
        creator = _creator(db, "cr@example.com")
        db.add(subscriber)
        db.commit()
        db.refresh(subscriber)
        sub = _active_subscription(db, subscriber.id, creator.id, NOW + timedelta(days=10))
        sub.cancel_at_period_end = True
        db.commit()

        service = SubscriptionService(db, provider=MockPaymentProvider())
        count = service.expire_canceled_subscriptions(now=NOW)
        db.refresh(sub)
        assert count == 0
        assert sub.status == SubscriptionStatus.active
        assert sub.cancel_at_period_end is True


def test_expire_sweep_flips_to_canceled_at_period_end(db_session):
    """Time-travel: at/after period end, the sweep expires it to canceled."""
    with db_session as db:
        subscriber = User(
            email="sub@example.com", username="sub", hashed_password="x",
            role=UserRole.registered, is_active=True,
        )
        creator = _creator(db, "cr@example.com")
        db.add(subscriber)
        db.commit()
        db.refresh(subscriber)
        sub = _active_subscription(db, subscriber.id, creator.id, NOW - timedelta(hours=1))
        sub.cancel_at_period_end = True
        db.commit()

        service = SubscriptionService(db, provider=MockPaymentProvider())
        count = service.expire_canceled_subscriptions(now=NOW)
        db.refresh(sub)
        assert count == 1
        assert sub.status == SubscriptionStatus.canceled
        assert sub.cancel_at_period_end is False


def test_expire_sweep_ignores_subscriptions_not_flagged(db_session):
    """Subscriptions without cancel_at_period_end are never expired by the sweep."""
    with db_session as db:
        subscriber = User(
            email="sub@example.com", username="sub", hashed_password="x",
            role=UserRole.registered, is_active=True,
        )
        creator = _creator(db, "cr@example.com")
        db.add(subscriber)
        db.commit()
        db.refresh(subscriber)
        sub = _active_subscription(db, subscriber.id, creator.id, NOW - timedelta(days=5))

        service = SubscriptionService(db, provider=MockPaymentProvider())
        count = service.expire_canceled_subscriptions(now=NOW)
        db.refresh(sub)
        assert count == 0
        assert sub.status == SubscriptionStatus.active


def test_expire_sweep_ignores_already_canceled(db_session):
    """A canceled (immediately) subscription is not re-touched by the sweep."""
    with db_session as db:
        subscriber = User(
            email="sub@example.com", username="sub", hashed_password="x",
            role=UserRole.registered, is_active=True,
        )
        creator = _creator(db, "cr@example.com")
        db.add(subscriber)
        db.commit()
        db.refresh(subscriber)
        sub = _active_subscription(db, subscriber.id, creator.id, NOW - timedelta(days=5))
        sub.status = SubscriptionStatus.canceled
        sub.cancel_at_period_end = True
        db.commit()

        service = SubscriptionService(db, provider=MockPaymentProvider())
        count = service.expire_canceled_subscriptions(now=NOW)
        db.refresh(sub)
        assert count == 0
        assert sub.status == SubscriptionStatus.canceled


# --------------------------------------------------------------------------- #
# Endpoint-level
# --------------------------------------------------------------------------- #

def test_cancel_endpoint_requires_auth(client, db_session):
    with db_session as db:
        creator = _creator(db)
        subscriber = User(
            email="sub@example.com", username="sub", hashed_password="x",
            role=UserRole.registered, is_active=True,
        )
        db.add(subscriber)
        db.commit()
        db.refresh(subscriber)
        sub = _active_subscription(db, subscriber.id, creator.id, NOW + timedelta(days=5))
        sub_id = sub.id
    resp = client.post("/cancel", json={"subscription_id": sub_id})
    assert resp.status_code == 401


def test_cancel_endpoint_marks_flag(client, db_session):
    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _creator(db)
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        sub = _active_subscription(db, subscriber.id, creator.id, NOW + timedelta(days=5))
        sub_id = sub.id

    resp = client.post("/cancel", json={"subscription_id": sub_id}, headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "active"  # access persists
    assert body["cancel_at_period_end"] is True


def test_cancel_endpoint_foreign_subscription_404(client, db_session):
    """A subscriber cannot cancel someone else's subscription."""
    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _creator(db)
        other = User(
            email="other@example.com", username="other", hashed_password="x",
            role=UserRole.registered, is_active=True,
        )
        db.add(other)
        db.commit()
        db.refresh(other)
        sub = _active_subscription(db, other.id, creator.id, NOW + timedelta(days=5))
        sub_id = sub.id
    resp = client.post("/cancel", json={"subscription_id": sub_id}, headers=headers)
    assert resp.status_code == 404


def test_cancel_endpoint_unknown_subscription_404(client, db_session):
    headers = _register(client, "sub@example.com")
    resp = client.post("/cancel", json={"subscription_id": 999999}, headers=headers)
    assert resp.status_code == 404


def test_cancel_endpoint_terminal_subscription_409(client, db_session):
    """An already-canceled/expired subscription cannot be cancelled again."""
    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _creator(db)
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        sub = _active_subscription(db, subscriber.id, creator.id, NOW + timedelta(days=5))
        sub.status = SubscriptionStatus.canceled
        db.commit()
        sub_id = sub.id

    resp = client.post("/cancel", json={"subscription_id": sub_id}, headers=headers)
    assert resp.status_code == 409


def test_cancel_endpoint_idempotent_for_live_subscription(client, db_session):
    """Cancelling a live subscription twice stays a 200 (flag already set)."""
    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _creator(db)
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        sub = _active_subscription(db, subscriber.id, creator.id, NOW + timedelta(days=5))
        sub_id = sub.id

    for _ in range(2):
        resp = client.post("/cancel", json={"subscription_id": sub_id}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["cancel_at_period_end"] is True


# --------------------------------------------------------------------------- #
# Integration: full flow with time travel
# --------------------------------------------------------------------------- #

def test_full_flow_cancel_then_expire(client, db_session):
    """Subscribe -> activate -> cancel-at-period-end -> (time passes) -> expired."""
    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _creator(db)
        creator_id = creator.id

    # Subscribe via the endpoint (mock provider -> incomplete).
    resp = client.post("/subscribe", json={"creator_id": creator_id}, headers=headers)
    assert resp.status_code == 201
    external_ref = resp.json()["subscription"]["external_ref"]

    provider = MockPaymentProvider()
    with db_session as db:
        service = SubscriptionService(db, provider=provider)
        sub = db.scalar(
            select(Subscription).where(Subscription.external_ref == external_ref)
        )
        # Simulate successful payment with a period ending "tomorrow".
        period_end = NOW + timedelta(days=1)
        sub.status = SubscriptionStatus.active
        sub.current_period_end = period_end
        db.commit()
        sub_id = sub.id

        # Cancel (non-renew).
        result = service.cancel_at_period_end(sub)
        db.refresh(result)
        assert result.cancel_at_period_end is True
        assert result.status == SubscriptionStatus.active

        # Time travels past period end -> sweep expires it.
        later = NOW + timedelta(days=2)
        count = service.expire_canceled_subscriptions(now=later)
        db.refresh(result)
        assert count == 1
        assert result.status == SubscriptionStatus.canceled
        assert result.cancel_at_period_end is False
        assert sub_id == result.id


def test_access_persists_after_cancel_until_expired(client, db_session):
    """After cancelling, viewer access remains until the sweep expires it."""
    from app.access import ViewerAccessLevel, resolve_viewer_access

    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _creator(db)
        creator_id = creator.id
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        sub = _active_subscription(
            db, subscriber.id, creator.id, NOW + timedelta(days=7)
        )
        sub.cancel_at_period_end = True
        db.commit()
        sub_id = sub.id

    # Viewer endpoint via the app (dependency override uses the same test DB).
    level_after_cancel = client.get(f"/creators/{creator_id}/access", headers=headers)
    assert level_after_cancel.json()["level"] == ViewerAccessLevel.follower.value

    # Time passes beyond the period end; sweep expires the subscription.
    with db_session as db:
        service = SubscriptionService(db, provider=MockPaymentProvider())
        sub = db.get(Subscription, sub_id)
        service.expire_canceled_subscriptions(now=NOW + timedelta(days=8))

    level_after_expiry = client.get(f"/creators/{creator_id}/access", headers=headers)
    assert level_after_expiry.json()["level"] == ViewerAccessLevel.registered.value
