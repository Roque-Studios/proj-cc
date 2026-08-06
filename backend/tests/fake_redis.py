"""In-memory Redis stand-in for tests (supports TTL expiry, no network).

Duck-types the subset of ``redis.Redis`` the watermark cache uses: ``get``,
``set(..., ex=)``, ``delete`` and ``ttl``. Entries expire on real wall-clock
time (``time.monotonic``), so TTL-eviction tests run against real expiry
semantics without needing a Redis server.
"""

from __future__ import annotations

import time


class FakeRedis:
    def __init__(self) -> None:
        # key -> (value, expires_at or None)
        self._store: dict[str, tuple[bytes, float | None]] = {}

    def _purge(self) -> None:
        now = time.monotonic()
        expired = [
            k for k, (_, exp) in self._store.items() if exp is not None and exp <= now
        ]
        for k in expired:
            del self._store[k]

    def get(self, key: str):
        self._purge()
        item = self._store.get(key)
        return item[0] if item is not None else None

    def set(self, key: str, value: bytes, ex: int | None = None) -> bool:
        expires_at = time.monotonic() + ex if ex is not None else None
        self._store[key] = (value, expires_at)
        return True

    def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    def ttl(self, key: str) -> int:
        self._purge()
        item = self._store.get(key)
        if item is None:
            return -2  # key missing
        if item[1] is None:
            return -1  # no expiry
        return max(0, int(item[1] - time.monotonic()))

    def clear(self) -> None:
        self._store.clear()
