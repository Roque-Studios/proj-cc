"""In-memory stand-in for the Redis pub/sub used by the realtime relay.

The relay (``app.realtime``) needs a sync ``publish`` plus an async pubsub
(``subscribe`` / ``get_message`` / ``close``). The hub keeps a queue per
(pubsub, channel) so every subscriber to a channel gets its own copy — exactly
what Redis delivers — and the fake is shared between two ``RealtimeManager``
instances to simulate cross-worker delivery.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque


class FakePubSub:
    """An async pubsub client bound to one hub subscription."""

    def __init__(self, hub: "FakePubSubHub") -> None:
        self.hub = hub
        self.channels: set[str] = set()
        self._queue: deque[tuple[str, str]] = deque()
        self.closed = False

    async def subscribe(self, *channels: str) -> None:
        self.channels.update(channels)

    async def get_message(self, ignore_subscribe_messages=True, timeout=0.2):
        """Poll the queue for up to ``timeout`` seconds, then return None.

        Mirrors ``redis.asyncio``: returns a ``{"type": "message", ...}`` dict
        (or None on timeout). The short poll keeps relay latency test-friendly.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue:
                channel, data = self._queue.popleft()
                return {"type": "message", "channel": channel, "data": data}
            await asyncio.sleep(0.005)
        return None

    async def close(self) -> None:
        self.closed = True
        self.hub._pubsubs.discard(self)


class FakeAsyncClient:
    """Async redis client stand-in (only ``pubsub`` is used by the relay)."""

    def __init__(self, hub: "FakePubSubHub") -> None:
        self.hub = hub

    def pubsub(self) -> FakePubSub:
        return self.hub.new_pubsub()


class FakeSyncClient:
    """Sync redis client stand-in (only ``publish`` is used by the relay)."""

    def __init__(self, hub: "FakePubSubHub") -> None:
        self.hub = hub

    def publish(self, channel: str, data) -> int:
        return self.hub.publish(channel, data)


class FakePubSubHub:
    """Shared channel bus: every pubsub subscribed to a channel gets the data."""

    def __init__(self) -> None:
        self._pubsubs: set[FakePubSub] = set()
        self.published: list[tuple[str, str]] = []

    def new_pubsub(self) -> FakePubSub:
        ps = FakePubSub(self)
        self._pubsubs.add(ps)
        return ps

    def async_client(self) -> FakeAsyncClient:
        return FakeAsyncClient(self)

    def sync_client(self) -> FakeSyncClient:
        return FakeSyncClient(self)

    def publish(self, channel: str, data) -> int:
        self.published.append((channel, data))
        subscribers = 0
        for ps in list(self._pubsubs):
            if channel in ps.channels:
                ps._queue.append((channel, data))
                subscribers += 1
        return subscribers

    def clear(self) -> None:
        self._pubsubs.clear()
        self.published.clear()
