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
