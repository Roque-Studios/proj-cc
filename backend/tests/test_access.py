"""Unit tests for viewer access-level classification.

Covers all three states (anonymous / registered / follower) for the
``GET /creators/{creator_id}/access`` endpoint, including the required edge
case: an expired subscription classifies as registered, not follower.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import Subscription, SubscriptionStatus, User, UserRole

SUBSCRIBER = {"email": "sub@example.com", "password": "Subscriber1"}


def _register_subscriber(client):
    resp = client.post("/auth/register", json=SUBSCRIBER)
    assert resp.status_code == 201
    token = client.post(
        "/auth/login",
        json={"email": SUBSCRIBER["email"], "password": SUBSCRIBER["password"]},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create_creator(db) -> User:
    creator = User(
        email="creator@example.com",
        username="creator",
        hashed_password="not-used-in-tests",
        role=UserRole.creator,
        is_active=True,
        is_creator=True,
    )
    db.add(creator)
    db.commit()
    db.refresh(creator)
    return creator


def _add_subscription(db, subscriber_id: int, creator_id: int, status, period_end=None):
    subscription = Subscription(
        subscriber_id=subscriber_id,
        creator_id=creator_id,
        status=status,
        current_period_end=period_end,
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return subscription


def _access(client, creator_id: int, headers=None):
    return client.get(f"/creators/{creator_id}/access", headers=headers)


# --------------------------------------------------------------------------- #
# Anonymous
# --------------------------------------------------------------------------- #

def test_anonymous_without_token(client, db_session):
    with db_session as db:
        creator = _create_creator(db)
        creator_id = creator.id
    resp = _access(client, creator_id)
    assert resp.status_code == 200
    assert resp.json()["level"] == "anonymous"
    assert resp.json()["user_id"] is None


def test_anonymous_with_garbage_token(client, db_session):
    with db_session as db:
        creator = _create_creator(db)
        creator_id = creator.id
    resp = _access(client, creator_id, headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 200
    assert resp.json()["level"] == "anonymous"


def test_anonymous_with_expired_token(client, db_session):
    """A validly-signed but expired access token resolves to anonymous, not 401."""
    from datetime import datetime, timedelta, timezone as tz
    from jose import jwt
    from app.config import settings

    with db_session as db:
        creator = _create_creator(db)
        creator_id = creator.id
    expired = jwt.encode(
        {
            "sub": "999",
            "type": "access",
            "exp": datetime.now(tz.utc) - timedelta(minutes=5),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    resp = _access(
        client, creator_id, headers={"Authorization": f"Bearer {expired}"}
    )
    assert resp.status_code == 200
    assert resp.json()["level"] == "anonymous"
    assert resp.json()["user_id"] is None


# --------------------------------------------------------------------------- #
# Registered
# --------------------------------------------------------------------------- #

def test_registered_with_valid_token(client, db_session):
    headers = _register_subscriber(client)
    with db_session as db:
        creator = _create_creator(db)
        creator_id = creator.id
    resp = _access(client, creator_id, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["level"] == "registered"
    assert resp.json()["user_id"] is not None
    assert resp.json()["creator"] is None  # registered viewer: creator not in context


def test_registered_user_with_subscription_to_another_creator(client, db_session):
    """Subscribing to creator A must not make the user a follower of creator B."""
    headers = _register_subscriber(client)
    with db_session as db:
        creator_a = _create_creator(db)
        creator_b = User(
            email="other@example.com",
            username="other",
            hashed_password="x",
            role=UserRole.creator,
            is_active=True,
            is_creator=True,
        )
        db.add(creator_b)
        db.commit()
        db.refresh(creator_b)
        subscriber = db.query(User).filter(User.email == SUBSCRIBER["email"]).one()
        _add_subscription(
            db,
            subscriber.id,
            creator_a.id,
            SubscriptionStatus.active,
            period_end=datetime.now(timezone.utc) + timedelta(days=30),
        )
        creator_a_id = creator_a.id
        creator_b_id = creator_b.id
    assert _access(client, creator_a_id, headers=headers).json()["level"] == "follower"
    assert _access(client, creator_b_id, headers=headers).json()["level"] == "registered"


# --------------------------------------------------------------------------- #
# Follower
# --------------------------------------------------------------------------- #

def test_follower_with_active_subscription(client, db_session):
    headers = _register_subscriber(client)
    with db_session as db:
        creator = _create_creator(db)
        subscriber = db.query(User).filter(User.email == SUBSCRIBER["email"]).one()
        _add_subscription(
            db,
            subscriber.id,
            creator.id,
            SubscriptionStatus.active,
            period_end=datetime.now(timezone.utc) + timedelta(days=30),
        )
        creator_id = creator.id
    resp = _access(client, creator_id, headers=headers)
    assert resp.json()["level"] == "follower"
    assert resp.json()["subscription"] == "active"
    assert resp.json()["creator"]["id"] == creator_id


def test_follower_of_non_creator_user_is_registered(client, db_session):
    """An active subscription to a non-creator user must not classify as follower."""
    headers = _register_subscriber(client)
    with db_session as db:
        plain_user = User(
            email="plain@example.com",
            username="plain",
            hashed_password="x",
            role=UserRole.registered,
            is_active=True,
            is_creator=False,
        )
        db.add(plain_user)
        db.commit()
        db.refresh(plain_user)
        subscriber = db.query(User).filter(User.email == SUBSCRIBER["email"]).one()
        _add_subscription(
            db,
            subscriber.id,
            plain_user.id,
            SubscriptionStatus.active,
            period_end=datetime.now(timezone.utc) + timedelta(days=30),
        )
        plain_user_id = plain_user.id
    resp = _access(client, plain_user_id, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["level"] == "registered"


def test_trialing_subscription_is_follower(client, db_session):
    headers = _register_subscriber(client)
    with db_session as db:
        creator = _create_creator(db)
        subscriber = db.query(User).filter(User.email == SUBSCRIBER["email"]).one()
        _add_subscription(
            db,
            subscriber.id,
            creator.id,
            SubscriptionStatus.trialing,
            period_end=datetime.now(timezone.utc) + timedelta(days=7),
        )
        creator_id = creator.id
    resp = _access(client, creator_id, headers=headers)
    assert resp.json()["level"] == "follower"
    assert resp.json()["subscription"] == "trialing"


# --------------------------------------------------------------------------- #
# Expired -> registered (acceptance edge case)
# --------------------------------------------------------------------------- #

def test_expired_status_is_registered_not_follower(client, db_session):
    headers = _register_subscriber(client)
    with db_session as db:
        creator = _create_creator(db)
        subscriber = db.query(User).filter(User.email == SUBSCRIBER["email"]).one()
        _add_subscription(
            db,
            subscriber.id,
            creator.id,
            SubscriptionStatus.expired,
            period_end=datetime.now(timezone.utc) - timedelta(days=1),
        )
        creator_id = creator.id
    resp = _access(client, creator_id, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["level"] == "registered"
    assert resp.json()["subscription"] is None


def test_past_period_active_status_is_registered_not_follower(client, db_session):
    """Status active but the current period has ended -> not a follower."""
    headers = _register_subscriber(client)
    with db_session as db:
        creator = _create_creator(db)
        subscriber = db.query(User).filter(User.email == SUBSCRIBER["email"]).one()
        _add_subscription(
            db,
            subscriber.id,
            creator.id,
            SubscriptionStatus.active,
            period_end=datetime.now(timezone.utc) - timedelta(days=1),
        )
        creator_id = creator.id
    resp = _access(client, creator_id, headers=headers)
    assert resp.json()["level"] == "registered"


def test_canceled_subscription_is_registered_not_follower(client, db_session):
    headers = _register_subscriber(client)
    with db_session as db:
        creator = _create_creator(db)
        subscriber = db.query(User).filter(User.email == SUBSCRIBER["email"]).one()
        _add_subscription(
            db,
            subscriber.id,
            creator.id,
            SubscriptionStatus.canceled,
            period_end=datetime.now(timezone.utc) + timedelta(days=10),
        )
        creator_id = creator.id
    resp = _access(client, creator_id, headers=headers)
    assert resp.json()["level"] == "registered"
