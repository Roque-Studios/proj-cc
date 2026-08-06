"""Real-time DM delivery: WebSocket connections + best-effort Redis relay.

Every process (gunicorn worker) keeps a **local socket registry** for the DM
WebSocket endpoint. Delivery is **local-first**: a message is pushed straight
to the recipient's sockets in this process, then broadcast on a Redis
pub/sub channel (``dm:user:{user_id}``) so the recipient's sockets in *other*
workers get it too. A per-process relay task subscribes to every connected
user's channel and forwards what arrives from Redis to the local sockets.

To avoid the local push and the relay both delivering in the same process,
the ids of messages delivered locally are remembered (a bounded deque) and the
relay skips those.

Resilience (mirrors the cache layer): Redis is **best-effort**. If publish
fails or the relay is down, the local push still delivers within this process
and the REST history endpoints (``GET /conversations/{id}/messages``) remain
the source of truth for anyone who was offline — that is the polling fallback.

The Redis clients are created through injectable factories so tests can swap
in an in-memory stand-in and simulate cross-worker delivery.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque

import redis
import structlog

from .config import settings

logger = structlog.get_logger()

_CHANNEL = "dm:user:{user_id}"

# Recent message ids already delivered locally — the relay skips these when the
# same process's Redis publish reflects back (prevents double delivery).
_DELIVERED_CACHE_SIZE = 500

# Upper bound on a single socket send: one slow/hung client must not stall
# delivery to the rest (or the REST route that awaits it).
_SEND_TIMEOUT_SECONDS = 5.0


def _channel_for(user_id: int) -> str:
    return _CHANNEL.format(user_id=user_id)


class RealtimeManager:
    def __init__(
        self,
        *,
        async_client_factory=None,
        sync_client_factory=None,
    ) -> None:
        # user_id -> live websockets in THIS process.
        self._connections: dict[int, set] = defaultdict(set)
        self._relay_task: asyncio.Task | None = None

        self._async_client = None
        self._pubsub = None
        self._sync_client: redis.Redis | None = None
        self._pending_channels: set[int] = set()
        self._subscribed: set[int] = set()
        self._relay_healthy = False
        # Once an outage has been logged we stay quiet (debug) until the relay
        # recovers — a sustained Redis failure shouldn't spam a warning per
        # retry (mirrors the cache layer's throttling).
        self._relay_unavailable_logged = False
        self._delivered_ids: deque[int] = deque(maxlen=_DELIVERED_CACHE_SIZE)

        self._async_client_factory = async_client_factory or self._default_async_client
        self._sync_client_factory = sync_client_factory or self._default_sync_client

    # ------------------------------------------------------------------ #
    # Connection lifecycle (called from the WS endpoint)
    # ------------------------------------------------------------------ #

    async def connect(self, user_id: int, ws) -> None:
        """Register a socket and make sure the relay is listening for this user."""
        self._connections[user_id].add(ws)
        if self._relay_task is None or self._relay_task.done():
            self._relay_task = asyncio.create_task(self._relay_loop())
        self._pending_channels.add(user_id)

    def disconnect(self, user_id: int, ws) -> None:
        sockets = self._connections.get(user_id)
        if sockets:
            sockets.discard(ws)
            if not sockets:
                del self._connections[user_id]
                # No live sockets left in this process: stop tracking the
                # channel so the relay never re-subscribes to every user that
                # ever connected. (The Redis-side subscription lingers
                # harmlessly until an outage cycle — or a reconnect re-adds the
                # user through ``connect``.)
                self._subscribed.discard(user_id)
                self._pending_channels.discard(user_id)

    def is_connected(self, user_id: int) -> bool:
        return bool(self._connections.get(user_id))

    def shutdown(self) -> None:
        """Cancel the relay task (used by tests)."""
        if self._relay_task is not None and not self._relay_task.done():
            self._relay_task.cancel()
        self._relay_task = None

    # ------------------------------------------------------------------ #
    # Delivery
    # ------------------------------------------------------------------ #

    async def send_to_user(self, user_id: int, payload: dict, *, message_id: int) -> None:
        """Deliver a message frame to the user's live sockets.

        Pushes to this process's sockets first (so delivery works even while
        Redis is down), then broadcasts on the user's Redis channel so other
        workers' relays deliver to sockets there. The relay skips the message
        id if this process already delivered it.

        The frame is stamped with a top-level ``id`` so the relay can dedupe
        regardless of the frame shape the caller builds (the message body is
        nested under ``message`` in the wire format).
        """
        if "id" not in payload:
            payload = {**payload, "id": message_id}
        delivered_local = await self._deliver_local(user_id, payload)
        if delivered_local:
            self._delivered_ids.append(message_id)
        self._publish(user_id, payload)

    async def _deliver_local(self, user_id: int, payload: dict) -> bool:
        sockets = list(self._connections.get(user_id, ()))
        if not sockets:
            return False
        dead = []
        for ws in sockets:
            try:
                await asyncio.wait_for(
                    ws.send_text(json.dumps(payload)), timeout=_SEND_TIMEOUT_SECONDS
                )
            except Exception:  # noqa: BLE001 - a dead/slow socket must not block others
                dead.append(ws)
        for ws in dead:
            self._connections[user_id].discard(ws)
        return len(dead) < len(sockets)

    # ------------------------------------------------------------------ #
    # Redis relay (cross-worker)
    # ------------------------------------------------------------------ #

    def _publish(self, user_id: int, payload: dict) -> None:
        """Broadcast a frame on the user's channel (best-effort, never raises)."""
        try:
            self._sync_client_factory().publish(
                _channel_for(user_id), json.dumps(payload)
            )
        except Exception as exc:  # noqa: BLE001 - publish must never break sends
            logger.debug("Realtime publish failed", user_id=user_id, error=str(exc))

    async def _relay_loop(self) -> None:
        """Subscribe to connected users' channels and forward to local sockets.

        Resilient: a Redis outage marks the relay unhealthy (the sender's local
        push still works) and retries; nothing in this loop may ever raise
        uncaught.
        """
        while True:
            try:
                if self._async_client is None:
                    self._async_client = self._async_client_factory()
                    self._pubsub = self._async_client.pubsub()
                new_channels = self._pending_channels - self._subscribed
                if new_channels:
                    await self._pubsub.subscribe(
                        *(_channel_for(uid) for uid in new_channels)
                    )
                    self._subscribed |= new_channels
                    self._relay_healthy = True
                    self._relay_unavailable_logged = False
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=0.2
                )
                if message is None or message.get("type") != "message":
                    continue
                try:
                    user_id = int(str(message["channel"]).rsplit(":", 1)[-1])
                except (TypeError, ValueError):
                    continue
                payload = json.loads(message["data"])
                message_id = payload.get("id")
                if message_id in self._delivered_ids:
                    # Already pushed locally when this process sent it.
                    self._delivered_ids.remove(message_id)
                    continue
                await self._deliver_local(user_id, payload)
            except Exception as exc:  # noqa: BLE001 - the relay must survive outages
                self._relay_healthy = False
                self._subscribed.clear()
                try:
                    if self._pubsub is not None:
                        await self._pubsub.close()
                except Exception:  # noqa: BLE001
                    pass
                self._pubsub = None
                self._async_client = None
                self._sync_client = None
                if not self._relay_unavailable_logged:
                    self._relay_unavailable_logged = True
                    logger.warning(
                        "Realtime relay unavailable — falling back to local delivery",
                        error=str(exc),
                    )
                else:
                    logger.debug("Realtime relay unavailable (repeated)", error=str(exc))
                await asyncio.sleep(1)

    # ------------------------------------------------------------------ #
    # Client factories (swappable in tests)
    # ------------------------------------------------------------------ #

    def _default_sync_client(self) -> redis.Redis:
        if self._sync_client is None:
            self._sync_client = redis.from_url(settings.REDIS_URL)
        return self._sync_client

    def _default_async_client(self):
        if self._async_client is None:
            import redis.asyncio as aredis

            self._async_client = aredis.from_url(settings.REDIS_URL)
        return self._async_client


# The process-wide singleton the routers use. Tests replace it (via
# ``realtime.manager``) with an in-memory variant.
manager = RealtimeManager()
