"""Unit tests for the Subscription data model (per-creator scope).

Covers: a user holding independent subscription statuses per creator, the
unique (subscriber_id, creator_id) constraint, and field persistence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Subscription, SubscriptionStatus, User, UserRole


def _create_user(db, email: str) -> User:
    user = User(
        email=email,
        username=email.split("@")[0],
        hashed_password="not-used-in-tests",
        role=UserRole.registered,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_my_subscriptions_endpoint_lists_rows_with_days_left(client, db_session):
    """The subscriber profile endpoint returns rows with creator + days left."""
    from datetime import timedelta

    from app.models import SubscriptionStatus

    # A creator and a subscriber via the API (creator needs a profile row for
    # the display name lookup).
    reg = client.post(
        "/auth/register",
        json={"email": "subme@example.com", "password": "StrongPass1"},
    )
    assert reg.status_code == 201
    token = client.post(
        "/auth/login",
        json={"email": "subme@example.com", "password": "StrongPass1"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    with db_session as db:
        # One creator per row (the (subscriber, creator) pair is unique).
        creator_a = _create_user(db, "creator-a@example.com")
        creator_a.role = UserRole.creator
        creator_a.is_creator = True
        creator_b = _create_user(db, "creator-b@example.com")
        creator_b.role = UserRole.creator
        creator_b.is_creator = True
        db.commit()

        active = Subscription(
            subscriber_id=_subscriber_id(client, headers),
            creator_id=creator_a.id,
            status=SubscriptionStatus.active,
            current_period_start=datetime.now(timezone.utc),
            current_period_end=datetime.now(timezone.utc) + timedelta(days=9),
            payment_provider="mock",
        )
        canceled = Subscription(
            subscriber_id=_subscriber_id(client, headers),
            creator_id=creator_b.id,
            status=SubscriptionStatus.canceled,
        )
        db.add_all([active, canceled])
        db.commit()
        active_id, canceled_id = active.id, canceled.id

    resp = client.get("/me/subscriptions", headers=headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    by_id = {i["subscription_id"]: i for i in items}
    assert set(by_id) == {active_id, canceled_id}
    active_out = by_id[active_id]
    assert active_out["creator_username"] == "creator-a"
    assert active_out["status"] == "active"
    assert active_out["days_left"] == 9
    # Non-active rows carry no days-left.
    assert by_id[canceled_id]["days_left"] is None


def test_my_subscriptions_requires_auth(client):
    assert client.get("/me/subscriptions").status_code == 401


def _subscriber_id(client, headers) -> int:
    return client.get("/auth/me", headers=headers).json()["id"]


def test_user_has_independent_subscriptions_per_creator(db_session):
    with db_session as db:
        subscriber = _create_user(db, "sub@example.com")
        creator_a = _create_user(db, "creator-a@example.com")
        creator_b = _create_user(db, "creator-b@example.com")

        db.add_all(
            [
                Subscription(
                    subscriber_id=subscriber.id,
                    creator_id=creator_a.id,
                    status=SubscriptionStatus.active,
                    current_period_start=datetime.now(timezone.utc),
                    current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
                ),
                Subscription(
                    subscriber_id=subscriber.id,
                    creator_id=creator_b.id,
                    status=SubscriptionStatus.canceled,
                ),
            ]
        )
        db.commit()

        rows = (
            db.query(Subscription)
            .filter(Subscription.subscriber_id == subscriber.id)
            .all()
        )
        assert len(rows) == 2
        by_creator = {row.creator_id: row.status for row in rows}
        assert by_creator[creator_a.id] == SubscriptionStatus.active
        assert by_creator[creator_b.id] == SubscriptionStatus.canceled


def test_duplicate_subscriber_creator_pair_rejected(db_session):
    with db_session as db:
        subscriber = _create_user(db, "sub@example.com")
        creator = _create_user(db, "creator@example.com")

        db.add(Subscription(subscriber_id=subscriber.id, creator_id=creator.id))
        db.commit()

        db.add(Subscription(subscriber_id=subscriber.id, creator_id=creator.id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_subscription_fields_persist(db_session):
    with db_session as db:
        subscriber = _create_user(db, "sub@example.com")
        creator = _create_user(db, "creator@example.com")

        subscription = Subscription(
            subscriber_id=subscriber.id,
            creator_id=creator.id,
            status=SubscriptionStatus.active,
            current_period_start=datetime(2026, 8, 1, tzinfo=timezone.utc),
            current_period_end=datetime(2026, 9, 1, tzinfo=timezone.utc),
            payment_provider="stripe",
            external_ref="sub_123abc",
        )
        db.add(subscription)
        db.commit()
        db.refresh(subscription)

        assert subscription.subscriber_id == subscriber.id
        assert subscription.creator_id == creator.id
        assert subscription.status == SubscriptionStatus.active
        assert subscription.current_period_start is not None
        assert subscription.current_period_end is not None
        assert subscription.payment_provider == "stripe"
        assert subscription.external_ref == "sub_123abc"


def test_subscription_default_status_is_active(db_session):
    with db_session as db:
        subscriber = _create_user(db, "sub@example.com")
        creator = _create_user(db, "creator@example.com")
        db.add(Subscription(subscriber_id=subscriber.id, creator_id=creator.id))
        db.commit()
        row = (
            db.query(Subscription)
            .filter(Subscription.subscriber_id == subscriber.id)
            .one()
        )
        assert row.status == SubscriptionStatus.active
