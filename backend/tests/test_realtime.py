"""Real-time DM delivery (WebSocket) tests.

Covers the acceptance: a message sent over WS is received in real time by the
connected recipient; a disconnected recipient gets it via the REST history on
reconnect (the polling fallback). Also: REST sends push live to connected
sockets, WS auth (``4401``), the DM gate over WS, ping/pong, and two
manager-level unit tests — cross-worker relay delivery through a shared
pub/sub hub, and same-process dedupe (no double delivery).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.models import (
    Conversation,
    CreatorProfile,
    Message,
    Subscription,
    SubscriptionStatus,
    User,
    UserRole,
)
from app.realtime import RealtimeManager
from tests.fake_realtime import FakePubSubHub

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


def _ws_token(headers: dict) -> str:
    return headers["Authorization"].split(" ")[1]


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


def _make_creator(db, email: str = "creator@example.com", *, allow_messages: bool = False) -> User:
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


def _subscribe_pair(client, db_session, *, allow_messages: bool = True) -> tuple[dict, dict, int, int]:
    """Registered creator + subscribed follower; returns headers + ids."""
    sub_headers = _register(client, "sub@example.com")
    with db_session as db:
        creator_headers = _api_creator(
            client, db, "creator@example.com", allow_messages=allow_messages
        )
        creator = db.scalar(select(User).where(User.email == "creator@example.com"))
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        _make_follower(db, subscriber.id, creator.id)
        creator_id = creator.id
        sub_id = subscriber.id
    return creator_headers, sub_headers, creator_id, sub_id


# --------------------------------------------------------------------------- #
# Acceptance: real-time WS delivery
# --------------------------------------------------------------------------- #


def test_ws_send_received_in_real_time(client, db_session):
    """Acceptance: a message sent over WS reaches the connected recipient live."""
    creator_headers, sub_headers, creator_id, sub_id = _subscribe_pair(client, db_session)

    creator_token = _ws_token(creator_headers)
    sub_token = _ws_token(sub_headers)
    with client.websocket_connect(f"/ws/dms?token={creator_token}") as cws:
        with client.websocket_connect(f"/ws/dms?token={sub_token}") as sws:
            cws.send_json(
                {"type": "send", "recipient_id": sub_id, "body": "hello live"}
            )

            # Sender gets the persisted-ack with the full message.
            ack = cws.receive_json()
            assert ack["type"] == "ack"
            assert ack["message"]["body"] == "hello live"
            assert ack["message"]["recipient_id"] == sub_id

            # The connected recipient receives it in real time.
            live = sws.receive_json()
            assert live["type"] == "message"
            assert live["message"]["sender_id"] == creator_id
            assert live["message"]["body"] == "hello live"

    # Persisted too (the fallback for anyone who was offline).
    with db_session as db:
        assert db.scalar(select(Message)) is not None


def test_rest_send_pushes_live_to_connected_recipient(client, db_session):
    """A REST send also reaches the recipient's live socket (any worker)."""
    creator_headers, sub_headers, _creator_id, sub_id = _subscribe_pair(client, db_session)

    with client.websocket_connect(f"/ws/dms?token={_ws_token(sub_headers)}") as sws:
        resp = client.post(
            "/messages",
            json={"recipient_id": sub_id, "body": "via rest"},
            headers=creator_headers,
        )
        assert resp.status_code == 201
        live = sws.receive_json()
        assert live["type"] == "message"
        assert live["message"]["body"] == "via rest"


def test_disconnected_recipient_fetches_on_reconnect(client, db_session):
    """Acceptance: a disconnected recipient gets the message via REST fetch on
    reconnect — the persisted history is the polling fallback."""
    creator_headers, sub_headers, _creator_id, sub_id = _subscribe_pair(client, db_session)

    # Nobody is connected: the message only persists.
    sent = client.post(
        "/messages",
        json={"recipient_id": sub_id, "body": "offline msg"},
        headers=creator_headers,
    )
    assert sent.status_code == 201
    conv_id = sent.json()["conversation_id"]

    # "Reconnect" (WS auth works, nothing was buffered live) — history has it.
    with client.websocket_connect(f"/ws/dms?token={_ws_token(sub_headers)}") as sws:
        sws.send_json({"type": "ping"})
        assert sws.receive_json()["type"] == "pong"
    history = client.get(f"/conversations/{conv_id}/messages", headers=sub_headers)
    assert history.status_code == 200
    assert [m["body"] for m in history.json()] == ["offline msg"]


# --------------------------------------------------------------------------- #
# WS auth + protocol
# --------------------------------------------------------------------------- #


def test_ws_requires_valid_token(client, db_session):
    """No token or a garbage token closes the connection with 4401."""
    for url in ("/ws/dms", "/ws/dms?token=not-a-jwt"):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(url) as ws:
                ws.receive_text()
        assert exc.value.code == 4401


def test_ws_ping_pong(client, db_session):
    sub_headers = _register(client, "sub@example.com")
    with client.websocket_connect(f"/ws/dms?token={_ws_token(sub_headers)}") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_ws_unknown_frame_type_error(client, db_session):
    sub_headers = _register(client, "sub@example.com")
    with client.websocket_connect(f"/ws/dms?token={_ws_token(sub_headers)}") as ws:
        ws.send_json({"type": "nonsense"})
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert "Unknown frame" in frame["detail"]


# --------------------------------------------------------------------------- #
# The DM gate over WS
# --------------------------------------------------------------------------- #


def test_ws_send_gate_enforced_non_follower(client, db_session):
    """A non-follower sending over WS gets an error frame; nothing persisted."""
    sub_headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _make_creator(db, "creator@example.com", allow_messages=True)
        creator_id = creator.id

    with client.websocket_connect(f"/ws/dms?token={_ws_token(sub_headers)}") as ws:
        ws.send_json({"type": "send", "recipient_id": creator_id, "body": "sneaky"})
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert "follower" in frame["detail"].lower()

    with db_session as db:
        assert db.scalar(select(Message)) is None
        assert db.scalar(select(Conversation)) is None


def test_ws_policy_block_clear_error(client, db_session):
    """The creator's messaging-off policy blocks a new WS thread with the same
    clear error as the REST path."""
    sub_headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _make_creator(db, "creator@example.com", allow_messages=False)
        creator_id = creator.id
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        _make_follower(db, subscriber.id, creator_id)

    with client.websocket_connect(f"/ws/dms?token={_ws_token(sub_headers)}") as ws:
        ws.send_json({"type": "send", "recipient_id": creator_id, "body": "hello"})
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert "messaging turned off" in frame["detail"]
        assert "existing conversation" in frame["detail"]


# --------------------------------------------------------------------------- #
# Manager-level: cross-worker relay + same-process dedupe
# --------------------------------------------------------------------------- #


async def _relay_scenario(manager_a, manager_b, socket, received, done) -> None:
    await manager_b.connect(42, socket)
    for _ in range(200):
        if 42 in manager_b._subscribed:
            break
        await asyncio.sleep(0.01)
    assert 42 in manager_b._subscribed, "manager B relay never subscribed"

    # A has no local sockets for user 42 — pure cross-worker path.
    await manager_a.send_to_user(
        42,
        {"type": "message", "message": {"body": "cross worker"}},
        message_id=999,
    )
    await asyncio.wait_for(done.wait(), timeout=3)
    manager_a.shutdown()
    manager_b.shutdown()


def test_cross_manager_relay_delivers_across_workers():
    """Two managers sharing one hub: a send on manager A reaches B's sockets —
    the gunicorn multi-worker case."""
    hub = FakePubSubHub()
    manager_a = RealtimeManager(
        async_client_factory=lambda: hub.async_client(),
        sync_client_factory=lambda: hub.sync_client(),
    )
    manager_b = RealtimeManager(
        async_client_factory=lambda: hub.async_client(),
        sync_client_factory=lambda: hub.sync_client(),
    )
    received: list[dict] = []
    done = asyncio.Event()

    class FakeSocket:
        async def send_text(self, data: str) -> None:
            received.append(json.loads(data))
            done.set()

    asyncio.run(_relay_scenario(manager_a, manager_b, FakeSocket(), received, done))
    assert received, "relay never delivered the frame"
    assert received[0]["type"] == "message"
    assert received[0]["message"]["body"] == "cross worker"


async def _dedupe_scenario(manager, socket, received) -> int:
    await manager.connect(7, socket)
    for _ in range(200):
        if 7 in manager._subscribed:
            break
        await asyncio.sleep(0.01)

    await manager.send_to_user(7, {"type": "message", "message": {"id": 1}}, message_id=1)
    await manager.send_to_user(7, {"type": "message", "message": {"id": 2}}, message_id=2)

    # Wait for the relay to consume the published copies (dedupe drain).
    for _ in range(200):
        if not manager._delivered_ids:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.3)  # settle: a phantom relay delivery would land here
    manager.shutdown()
    return len(received)


def test_same_manager_no_double_delivery():
    """Local push + relay feedback never double-deliver in one process."""
    hub = FakePubSubHub()
    manager = RealtimeManager(
        async_client_factory=lambda: hub.async_client(),
        sync_client_factory=lambda: hub.sync_client(),
    )
    received: list[dict] = []

    class FakeSocket:
        async def send_text(self, data: str) -> None:
            received.append(json.loads(data))

    count = asyncio.run(_dedupe_scenario(manager, FakeSocket(), received))
    assert count == 2, f"expected exactly 2 frames, got {count}"


def test_local_delivery_survives_redis_outage():
    """Best-effort: a failing Redis (publish + relay) never blocks delivery."""
    received: list[dict] = []

    def broken_client():
        raise RuntimeError("redis is down")

    manager = RealtimeManager(
        async_client_factory=broken_client,
        sync_client_factory=broken_client,
    )

    class FakeSocket:
        async def send_text(self, data: str) -> None:
            received.append(json.loads(data))

    async def scenario():
        await manager.connect(7, FakeSocket())
        await manager.send_to_user(
            7, {"type": "message", "message": {"body": "offline redis"}}, message_id=5
        )
        await asyncio.sleep(0.3)  # the failing relay retries in the background
        manager.shutdown()

    asyncio.run(scenario())
    assert [f["message"]["body"] for f in received] == ["offline redis"]


def test_relay_tracking_pruned_on_disconnect():
    """A user with no live sockets left stops being tracked by the relay."""
    hub = FakePubSubHub()
    manager = RealtimeManager(
        async_client_factory=lambda: hub.async_client(),
        sync_client_factory=lambda: hub.sync_client(),
    )

    class FakeSocket:
        async def send_text(self, data: str) -> None:
            pass

    async def scenario() -> tuple[bool, bool, bool]:
        socket = FakeSocket()
        await manager.connect(9, socket)
        for _ in range(200):
            if 9 in manager._subscribed:
                break
            await asyncio.sleep(0.01)
        assert 9 in manager._subscribed
        manager.disconnect(9, socket)
        pruned = (
            9 not in manager._subscribed,
            9 not in manager._pending_channels,
            9 not in manager._connections,
        )
        manager.shutdown()
        return pruned

    assert all(asyncio.run(scenario()))
