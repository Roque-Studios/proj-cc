"""Creator messaging-settings toggle tests.

Covers the acceptance: toggling ``allow_messages_from_all_followers`` takes
effect **immediately** for new message attempts (a follower blocked before the
toggle can send right after it flips on, and vice versa), existing threads are
**unaffected** (continuing a conversation stays allowed after a toggle off),
plus the endpoint guards and default state.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import CreatorProfile, Subscription, SubscriptionStatus, User, UserRole

NOW = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)


def _register(client, email: str, password: str = "Passw0rd1") -> dict:
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201
    token = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _api_creator(client, db, email: str = "creator@example.com") -> dict:
    """A creator registered through the API (real login) + role + profile."""
    headers = _register(client, email)
    user = db.scalar(select(User).where(User.email == email))
    user.role = UserRole.creator
    user.is_creator = True
    db.add(CreatorProfile(user_id=user.id, display_name=email.split("@")[0]))
    db.commit()
    return headers


def _make_follower(db, subscriber_id: int, creator_id: int) -> None:
    db.add(
        Subscription(
            subscriber_id=subscriber_id,
            creator_id=creator_id,
            status=SubscriptionStatus.active,
            current_period_start=NOW - timedelta(days=10),
            current_period_end=NOW + timedelta(days=20),
            payment_provider="mock",
            external_ref=f"sub_mock_{subscriber_id}_{creator_id}",
        )
    )
    db.commit()


# --------------------------------------------------------------------------- #
# Endpoint guards + default state
# --------------------------------------------------------------------------- #


def test_messaging_settings_requires_auth(client):
    assert client.get("/creator/messaging-settings").status_code == 401
    assert (
        client.put(
            "/creator/messaging-settings",
            json={"allow_messages_from_all_followers": True},
        ).status_code
        == 401
    )


def test_messaging_settings_requires_creator_role(client):
    headers = _register(client, "reg@example.com")
    assert (
        client.get("/creator/messaging-settings", headers=headers).status_code == 403
    )
    assert (
        client.put(
            "/creator/messaging-settings",
            json={"allow_messages_from_all_followers": True},
            headers=headers,
        ).status_code
        == 403
    )


def test_messaging_settings_defaults_to_off(client, db_session):
    creator_headers = _api_creator(client, db_session, "creator@example.com")
    resp = client.get("/creator/messaging-settings", headers=creator_headers)
    assert resp.status_code == 200
    assert resp.json() == {"allow_messages_from_all_followers": False}


def test_messaging_settings_round_trip(client, db_session):
    creator_headers = _api_creator(client, db_session, "creator@example.com")
    on = client.put(
        "/creator/messaging-settings",
        json={"allow_messages_from_all_followers": True},
        headers=creator_headers,
    )
    assert on.status_code == 200
    assert on.json()["allow_messages_from_all_followers"] is True
    off = client.put(
        "/creator/messaging-settings",
        json={"allow_messages_from_all_followers": False},
        headers=creator_headers,
    )
    assert off.status_code == 200
    assert off.json()["allow_messages_from_all_followers"] is False
    # Persisted, not just echoed.
    with db_session as db:
        profile = db.scalar(
            select(CreatorProfile).where(
                CreatorProfile.user_id == db.scalar(
                    select(User.id).where(User.email == "creator@example.com")
                )
            )
        )
        assert profile.allow_messages_from_all_followers is False


# --------------------------------------------------------------------------- #
# Toggle takes effect immediately (acceptance)
# --------------------------------------------------------------------------- #


def test_toggle_on_immediately_allows_new_threads(client, db_session):
    """A follower blocked while off can message right after the toggle flips on."""
    sub_headers = _register(client, "sub@example.com")
    with db_session as db:
        creator_headers = _api_creator(client, db, "creator@example.com")
        creator_id = db.scalar(
            select(User.id).where(User.email == "creator@example.com")
        )
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        _make_follower(db, subscriber.id, creator_id)

    # Default off -> blocked with the clear error.
    blocked = client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "hello"},
        headers=sub_headers,
    )
    assert blocked.status_code == 403
    assert "messaging turned off" in blocked.json()["detail"]

    # Toggle on -> the very next attempt from the same follower succeeds.
    toggle = client.put(
        "/creator/messaging-settings",
        json={"allow_messages_from_all_followers": True},
        headers=creator_headers,
    )
    assert toggle.status_code == 200

    sent = client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "hello"},
        headers=sub_headers,
    )
    assert sent.status_code == 201


def test_toggle_off_immediately_blocks_new_threads(client, db_session):
    """After the toggle flips off, a follower with no thread is blocked at once."""
    with db_session as db:
        creator_headers = _api_creator(client, db, "creator@example.com")
        creator_id = db.scalar(
            select(User.id).where(User.email == "creator@example.com")
        )
    assert (
        client.put(
            "/creator/messaging-settings",
            json={"allow_messages_from_all_followers": True},
            headers=creator_headers,
        ).status_code
        == 200
    )

    # A new follower starts a thread while the setting is on.
    sub_a_headers = _register(client, "sub-a@example.com")
    with db_session as db:
        sub_a = db.scalar(select(User).where(User.email == "sub-a@example.com"))
        _make_follower(db, sub_a.id, creator_id)
    first = client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "from A"},
        headers=sub_a_headers,
    )
    assert first.status_code == 201

    # Toggle off -> a different follower (no thread) is blocked immediately.
    assert (
        client.put(
            "/creator/messaging-settings",
            json={"allow_messages_from_all_followers": False},
            headers=creator_headers,
        ).status_code
        == 200
    )
    sub_b_headers = _register(client, "sub-b@example.com")
    with db_session as db:
        sub_b = db.scalar(select(User).where(User.email == "sub-b@example.com"))
        _make_follower(db, sub_b.id, creator_id)
    blocked = client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "from B"},
        headers=sub_b_headers,
    )
    assert blocked.status_code == 403
    assert "messaging turned off" in blocked.json()["detail"]


def test_existing_threads_unaffected_by_toggle_off(client, db_session):
    """Acceptance: toggling off never cuts off an in-flight conversation."""
    sub_headers = _register(client, "sub@example.com")
    with db_session as db:
        creator_headers = _api_creator(client, db, "creator@example.com")
        creator_id = db.scalar(
            select(User.id).where(User.email == "creator@example.com")
        )
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        _make_follower(db, subscriber.id, creator_id)
        sub_id = subscriber.id

    # The creator starts the thread (creates the "existing conversation").
    started = client.post(
        "/messages",
        json={"recipient_id": sub_id, "body": "welcome"},
        headers=creator_headers,
    )
    assert started.status_code == 201

    # Toggle off.
    assert (
        client.put(
            "/creator/messaging-settings",
            json={"allow_messages_from_all_followers": False},
            headers=creator_headers,
        ).status_code
        == 200
    )

    # The existing thread is unaffected: the subscriber can keep replying.
    reply = client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "thanks"},
        headers=sub_headers,
    )
    assert reply.status_code == 201
    assert reply.json()["conversation_id"] == started.json()["conversation_id"]


def test_toggle_off_does_not_block_creator_outbound(client, db_session):
    """The creator's own outbound never depends on the toggle."""
    with db_session as db:
        creator_headers = _api_creator(client, db, "creator@example.com")
        subscriber = User(
            email="sub@example.com",
            username="sub",
            hashed_password="x",
            role=UserRole.registered,
            is_active=True,
        )
        db.add(subscriber)
        db.commit()
        db.refresh(subscriber)
        sub_id = subscriber.id
    client.put(
        "/creator/messaging-settings",
        json={"allow_messages_from_all_followers": False},
        headers=creator_headers,
    )
    resp = client.post(
        "/messages",
        json={"recipient_id": sub_id, "body": "still here"},
        headers=creator_headers,
    )
    assert resp.status_code == 201
