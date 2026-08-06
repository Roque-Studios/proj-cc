"""DM data-model + messaging gate tests.

Covers the acceptance: a message is blocked with a clear error when the
creator's ``allow_messages_from_all_followers`` setting is off **and** the
sender isn't already in an existing thread; allowed when the setting is on, or
when the thread already exists. Also: the follower requirement, the creator's
ability to start a thread, thread grouping (the unique creator+subscriber
pair), and the conversation read endpoints.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models import (
    Conversation,
    CreatorProfile,
    Message,
    Subscription,
    SubscriptionStatus,
    User,
    UserRole,
)

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


def _make_creator(
    db,
    email: str = "creator@example.com",
    *,
    allow_messages: bool = False,
) -> User:
    """A creator row (no login credentials — only for DB-level setup)."""
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
    db.add(
        CreatorProfile(
            user_id=creator.id,
            display_name=email.split("@")[0],
            allow_messages_from_all_followers=allow_messages,
        )
    )
    db.commit()
    return creator


def _api_creator(
    client,
    db,
    email: str = "creator@example.com",
    *,
    allow_messages: bool = False,
) -> dict:
    """A creator registered through the API (real login) + role + profile."""
    headers = _register(client, email)
    user = db.scalar(select(User).where(User.email == email))
    user.role = UserRole.creator
    user.is_creator = True
    db.add(
        CreatorProfile(
            user_id=user.id,
            display_name=email.split("@")[0],
            allow_messages_from_all_followers=allow_messages,
        )
    )
    db.commit()
    return headers


def _make_subscriber(db, email: str = "sub@example.com") -> User:
    subscriber = User(
        email=email,
        username=email.split("@")[0],
        hashed_password="x",
        role=UserRole.registered,
        is_active=True,
    )
    db.add(subscriber)
    db.commit()
    db.refresh(subscriber)
    return subscriber


def _make_follower(db, subscriber_id: int, creator_id: int) -> None:
    """An active subscription with a current period (a follower)."""
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


def _make_lapsed_follower(db, subscriber_id: int, creator_id: int) -> None:
    """An active-status subscription whose period already ended (not a follower)."""
    db.add(
        Subscription(
            subscriber_id=subscriber_id,
            creator_id=creator_id,
            status=SubscriptionStatus.active,
            current_period_start=NOW - timedelta(days=30),
            current_period_end=NOW - timedelta(days=1),
            payment_provider="mock",
            external_ref=f"sub_mock_lapsed_{subscriber_id}_{creator_id}",
        )
    )
    db.commit()


# --------------------------------------------------------------------------- #
# Auth + basic validation
# --------------------------------------------------------------------------- #


def test_send_requires_auth(client, db_session):
    resp = client.post(
        "/messages",
        json={"recipient_id": 1, "body": "hi"},
    )
    assert resp.status_code == 401


def test_send_to_unknown_recipient_404(client, db_session):
    headers = _register(client, "sender@example.com")
    resp = client.post(
        "/messages",
        json={"recipient_id": 999999, "body": "hi"},
        headers=headers,
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_send_to_non_creator_400(client, db_session):
    headers = _register(client, "sender@example.com")
    with db_session as db:
        other = _make_subscriber(db, "other@example.com")
        other_id = other.id
    resp = client.post(
        "/messages",
        json={"recipient_id": other_id, "body": "hi"},
        headers=headers,
    )
    assert resp.status_code == 400


def test_send_to_self_400(client, db_session):
    headers = _register(client, "me@example.com")
    with db_session as db:
        me = db.scalar(select(User).where(User.email == "me@example.com"))
        me.role = UserRole.creator
        me.is_creator = True
        db.commit()
        me_id = me.id
    resp = client.post(
        "/messages",
        json={"recipient_id": me_id, "body": "hi me"},
        headers=headers,
    )
    assert resp.status_code == 400


def test_send_blank_body_rejected(client, db_session):
    headers = _register(client, "sender@example.com")
    with db_session as db:
        creator = _make_creator(db, "creator@example.com", allow_messages=True)
        creator_id = creator.id
    # Schema-level: whitespace-only body is rejected up front.
    resp = client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "   "},
        headers=headers,
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# The messaging gate (acceptance)
# --------------------------------------------------------------------------- #


def test_subscriber_must_be_follower_403(client, db_session):
    """A registered non-follower is blocked regardless of the creator's policy."""
    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _make_creator(db, "creator@example.com", allow_messages=True)
        creator_id = creator.id
    resp = client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "hello"},
        headers=headers,
    )
    assert resp.status_code == 403
    assert "follower" in resp.json()["detail"].lower()


def test_setting_off_no_thread_blocked_with_clear_error(client, db_session):
    """Acceptance: setting disabled + no existing thread -> blocked, clear error."""
    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _make_creator(db, "creator@example.com", allow_messages=False)
        creator_id = creator.id
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        _make_follower(db, subscriber.id, creator_id)

    resp = client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "hello"},
        headers=headers,
    )
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert "messaging turned off" in detail
    assert "existing conversation" in detail
    # Nothing was persisted.
    with db_session as db:
        assert db.scalar(select(Conversation)) is None
        assert db.scalar(select(Message)) is None


def test_setting_on_allows_new_thread(client, db_session):
    """Setting enabled: a follower may start a conversation."""
    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _make_creator(db, "creator@example.com", allow_messages=True)
        creator_id = creator.id
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        _make_follower(db, subscriber.id, creator_id)

    first = client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "hello"},
        headers=headers,
    )
    assert first.status_code == 201
    second = client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "second message"},
        headers=headers,
    )
    assert second.status_code == 201
    # Thread grouping: both messages share one conversation.
    assert first.json()["conversation_id"] == second.json()["conversation_id"]
    with db_session as db:
        assert db.scalar(select(func.count()).select_from(Conversation)) == 1


def test_setting_off_with_existing_thread_allowed(client, db_session):
    """Acceptance: with the setting off, an existing thread still lets the
    subscriber reply — the creator started the conversation first."""
    sub_headers = _register(client, "sub@example.com")
    with db_session as db:
        creator_headers = _api_creator(
            client, db, "creator@example.com", allow_messages=False
        )
        creator = db.scalar(select(User).where(User.email == "creator@example.com"))
        creator_id = creator.id
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        _make_follower(db, subscriber.id, creator_id)
        sub_id = subscriber.id

    # The creator initiates -> the thread now exists.
    started = client.post(
        "/messages",
        json={"recipient_id": sub_id, "body": "welcome"},
        headers=creator_headers,
    )
    assert started.status_code == 201

    # Now the subscriber can reply even though the setting is off.
    reply = client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "thanks!"},
        headers=sub_headers,
    )
    assert reply.status_code == 201
    assert reply.json()["conversation_id"] == started.json()["conversation_id"]


def test_creator_can_message_any_active_subscriber(client, db_session):
    """The creator's outbound is never gated by the policy."""
    with db_session as db:
        creator_headers = _api_creator(
            client, db, "creator@example.com", allow_messages=False
        )
        subscriber = _make_subscriber(db, "sub@example.com")
        sub_id = subscriber.id
    resp = client.post(
        "/messages",
        json={"recipient_id": sub_id, "body": "hey"},
        headers=creator_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["recipient_id"] == sub_id


def test_creator_cannot_message_another_creator(client, db_session):
    """Two creators can't be in a DM — the thread pair is directional and would
    split into two conversations, so it's rejected up front."""
    with db_session as db:
        creator_a_headers = _api_creator(client, db, "a@example.com")
        _api_creator(client, db, "b@example.com")
        b_id = db.scalar(select(User).where(User.email == "b@example.com")).id
    resp = client.post(
        "/messages",
        json={"recipient_id": b_id, "body": "yo"},
        headers=creator_a_headers,
    )
    assert resp.status_code == 400


def test_lapsed_subscriber_blocked_for_new_thread(client, db_session):
    """A lapsed subscription is not a follower: starting a thread is blocked."""
    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _make_creator(db, "creator@example.com", allow_messages=True)
        creator_id = creator.id
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        _make_lapsed_follower(db, subscriber.id, creator_id)
    resp = client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "hello"},
        headers=headers,
    )
    assert resp.status_code == 403
    assert "follower" in resp.json()["detail"].lower()


def test_lapsed_subscriber_can_continue_existing_thread(client, db_session):
    """The acceptance carve-out: an existing thread lets the subscriber reply
    even after their subscription lapsed (thread continuity)."""
    sub_headers = _register(client, "sub@example.com")
    with db_session as db:
        creator_headers = _api_creator(
            client, db, "creator@example.com", allow_messages=False
        )
        creator = db.scalar(select(User).where(User.email == "creator@example.com"))
        creator_id = creator.id
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        _make_follower(db, subscriber.id, creator_id)  # active at first
        sub_id = subscriber.id

    started = client.post(
        "/messages",
        json={"recipient_id": sub_id, "body": "welcome"},
        headers=creator_headers,
    )
    assert started.status_code == 201

    # The subscription lapses after the thread was created.
    with db_session as db:
        sub_row = db.scalar(
            select(Subscription).where(Subscription.subscriber_id == sub_id)
        )
        sub_row.current_period_end = NOW - timedelta(days=1)
        db.commit()

    reply = client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "still here"},
        headers=sub_headers,
    )
    assert reply.status_code == 201
    assert reply.json()["conversation_id"] == started.json()["conversation_id"]


# --------------------------------------------------------------------------- #
# Thread grouping + read endpoints
# --------------------------------------------------------------------------- #


def test_thread_grouping_per_creator_subscriber_pair(client, db_session):
    """Each (creator, subscriber) pair gets its own conversation."""
    sub1_headers = _register(client, "sub1@example.com")
    sub2_headers = _register(client, "sub2@example.com")
    with db_session as db:
        creator = _make_creator(db, "creator@example.com", allow_messages=True)
        creator_id = creator.id
        sub1 = db.scalar(select(User).where(User.email == "sub1@example.com"))
        sub2 = db.scalar(select(User).where(User.email == "sub2@example.com"))
        _make_follower(db, sub1.id, creator_id)
        _make_follower(db, sub2.id, creator_id)

    conv1 = client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "from sub1"},
        headers=sub1_headers,
    ).json()["conversation_id"]
    conv2 = client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "from sub2"},
        headers=sub2_headers,
    ).json()["conversation_id"]
    assert conv1 != conv2

    # Same pair stays in conv1.
    again = client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "sub1 again"},
        headers=sub1_headers,
    ).json()["conversation_id"]
    assert again == conv1


def test_conversation_listing_requires_auth(client):
    assert client.get("/conversations").status_code == 401


def test_conversation_listing_shows_both_sides(client, db_session):
    sub_headers = _register(client, "sub@example.com")
    with db_session as db:
        creator_headers = _api_creator(
            client, db, "creator@example.com", allow_messages=True
        )
        creator = db.scalar(select(User).where(User.email == "creator@example.com"))
        creator_id = creator.id
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        _make_follower(db, subscriber.id, creator_id)
        subscriber_id = subscriber.id
    client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "hello"},
        headers=sub_headers,
    )

    for headers in (sub_headers, creator_headers):
        resp = client.get("/conversations", headers=headers)
        assert resp.status_code == 200
        convos = resp.json()
        assert len(convos) == 1
        convo = convos[0]
        assert convo["creator_id"] == creator_id
        assert convo["subscriber_id"] == subscriber_id
        assert convo["last_message"]["body"] == "hello"
        # ``other`` is the counterparty from the requester's perspective.
        if headers is sub_headers:
            assert convo["other"]["id"] == creator_id
        else:
            assert convo["other"]["id"] == subscriber_id


def test_conversation_messages_history_participants_only(client, db_session):
    sub_headers = _register(client, "sub@example.com")
    outsider_headers = _register(client, "outsider@example.com")
    with db_session as db:
        creator_headers = _api_creator(
            client, db, "creator@example.com", allow_messages=True
        )
        creator = db.scalar(select(User).where(User.email == "creator@example.com"))
        creator_id = creator.id
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        _make_follower(db, subscriber.id, creator_id)

    conv_id = client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "first"},
        headers=sub_headers,
    ).json()["conversation_id"]
    client.post(
        "/messages",
        json={"recipient_id": creator_id, "body": "second"},
        headers=sub_headers,
    )

    # Outsider gets the same 404 as a missing thread (no id leak).
    assert (
        client.get(f"/conversations/{conv_id}/messages", headers=outsider_headers).status_code
        == 404
    )
    assert client.get("/conversations/999999/messages", headers=sub_headers).status_code == 404

    # Participant sees the history, oldest first (paginated response shape).
    history = client.get(f"/conversations/{conv_id}/messages", headers=sub_headers)
    assert history.status_code == 200
    page = history.json()
    assert [m["body"] for m in page["messages"]] == ["first", "second"]
    assert page["before_id"] is not None
    assert page["has_more"] is False

    # The creator sees the same thread.
    creator_history = client.get(
        f"/conversations/{conv_id}/messages", headers=creator_headers
    )
    assert creator_history.status_code == 200


# --------------------------------------------------------------------------- #
# Message history pagination (scroll-up cursor)
# --------------------------------------------------------------------------- #


def _fill_conversation(client, headers, recipient_id: int, n: int) -> int:
    """Send ``n`` messages; returns the conversation id."""
    conv_id = None
    for i in range(n):
        resp = client.post(
            "/messages",
            json={"recipient_id": recipient_id, "body": f"msg-{i}"},
            headers=headers,
        )
        assert resp.status_code == 201
        conv_id = resp.json()["conversation_id"]
    return conv_id


def test_messages_pagination_newest_page_then_older(client, db_session):
    sub_headers = _register(client, "sub@example.com")
    with db_session as db:
        creator_headers = _api_creator(
            client, db, "creator@example.com", allow_messages=True
        )
        creator = db.scalar(select(User).where(User.email == "creator@example.com"))
        creator_id = creator.id
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        _make_follower(db, subscriber.id, creator_id)

    conv_id = _fill_conversation(client, sub_headers, creator_id, 5)

    # First page: the NEWEST ``limit`` messages, oldest-first within the page.
    first = client.get(
        f"/conversations/{conv_id}/messages?limit=2", headers=sub_headers
    ).json()
    assert [m["body"] for m in first["messages"]] == ["msg-3", "msg-4"]
    assert first["has_more"] is True

    # Scroll up: the two before the cursor.
    second = client.get(
        f"/conversations/{conv_id}/messages?limit=2&before_id={first['before_id']}",
        headers=sub_headers,
    ).json()
    assert [m["body"] for m in second["messages"]] == ["msg-1", "msg-2"]
    assert second["has_more"] is True

    # Oldest page: one message, no more history.
    third = client.get(
        f"/conversations/{conv_id}/messages?limit=2&before_id={second['before_id']}",
        headers=sub_headers,
    ).json()
    assert [m["body"] for m in third["messages"]] == ["msg-0"]
    assert third["has_more"] is False


def test_messages_pagination_validates_parameters(client, db_session):
    sub_headers = _register(client, "sub@example.com")
    with db_session as db:
        creator_headers = _api_creator(
            client, db, "creator@example.com", allow_messages=True
        )
        creator = db.scalar(select(User).where(User.email == "creator@example.com"))
        creator_id = creator.id
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        _make_follower(db, subscriber.id, creator_id)
    conv_id = _fill_conversation(client, sub_headers, creator_id, 2)

    assert (
        client.get(
            f"/conversations/{conv_id}/messages?limit=0", headers=sub_headers
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"/conversations/{conv_id}/messages?limit=500", headers=sub_headers
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"/conversations/{conv_id}/messages?before_id=0", headers=sub_headers
        ).status_code
        == 422
    )


# --------------------------------------------------------------------------- #
# Messaging status (chat input gate)
# --------------------------------------------------------------------------- #


def test_messages_status_requires_auth(client, db_session):
    assert client.get("/messages/status?recipient_id=1").status_code == 401


def test_messages_status_unknown_recipient_404(client, db_session):
    headers = _register(client, "sender@example.com")
    assert (
        client.get("/messages/status?recipient_id=999999", headers=headers).status_code
        == 404
    )


def test_messages_status_subscriber_to_creator(client, db_session):
    """Follower + policy on -> can message; non-follower -> blocked with reason."""
    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _make_creator(db, "creator@example.com", allow_messages=True)
        creator_id = creator.id
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))

    # Not a follower yet.
    resp = client.get(f"/messages/status?recipient_id={creator_id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["recipient_is_creator"] is True
    assert body["is_follower"] is False
    assert body["can_message"] is False
    assert "follower" in body["reason"].lower()

    # Become a follower -> allowed.
    with db_session as db:
        _make_follower(db, subscriber.id, creator_id)
    resp = client.get(f"/messages/status?recipient_id={creator_id}", headers=headers)
    assert resp.json()["can_message"] is True


def test_messages_status_policy_off_explains_disabled(client, db_session):
    """The disabled-messaging state the chat UI renders instead of a composer."""
    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _make_creator(db, "creator@example.com", allow_messages=False)
        creator_id = creator.id
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        _make_follower(db, subscriber.id, creator_id)
        subscriber_id = subscriber.id

    resp = client.get(f"/messages/status?recipient_id={creator_id}", headers=headers)
    body = resp.json()
    assert body["messaging_enabled"] is False
    assert body["can_message"] is False
    assert "messaging turned off" in body["reason"].lower()

    # With an existing thread the carve-out applies.
    with db_session as db:
        db.add(
            Conversation(
                creator_id=creator_id,
                subscriber_id=subscriber_id,
            )
        )
        db.commit()
    resp = client.get(f"/messages/status?recipient_id={creator_id}", headers=headers)
    assert resp.json()["can_message"] is True


def test_messages_status_creator_can_message_subscriber(client, db_session):
    with db_session as db:
        creator_headers = _api_creator(client, db, "creator@example.com")
        subscriber = _make_subscriber(db, "sub@example.com")
        sub_id = subscriber.id

    resp = client.get(f"/messages/status?recipient_id={sub_id}", headers=creator_headers)
    body = resp.json()
    assert body["recipient_is_creator"] is False
    assert body["can_message"] is True


def test_messages_status_self_and_creator_to_creator(client, db_session):
    with db_session as db:
        creator_a_headers = _api_creator(client, db, "a@example.com")
        _api_creator(client, db, "b@example.com")
        b_id = db.scalar(select(User).where(User.email == "b@example.com")).id
        a_id = db.scalar(select(User).where(User.email == "a@example.com")).id

    resp = client.get(f"/messages/status?recipient_id={b_id}", headers=creator_a_headers)
    assert resp.json()["can_message"] is False

    resp = client.get(f"/messages/status?recipient_id={a_id}", headers=creator_a_headers)
    assert resp.json()["can_message"] is False
