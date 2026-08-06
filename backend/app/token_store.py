"""JWT token revocation store (Redis-backed denylist).

Tokens carry a unique ``jti`` claim. Revoking a token adds its ``jti`` to a
Redis key with a TTL equal to the token's remaining lifetime, so expiry and
revocation stay in sync.

Rotation uses an atomic ``consume`` (Redis ``SET NX``): only the first use of a
refresh token succeeds, making single-use rotation race-safe.

Failure policy: read checks (``is_token_revoked``) fail OPEN (log + allow) so a
Redis blip never locks authenticated users out; revocations (``consume``) fail
CLOSED (propagate) because a write that cannot be persisted must not be
reported as done. Redis remains a hard runtime dependency surfaced by
``/health``.

An in-memory implementation is provided as a test double (see
``reset_store_for_tests``).
"""

from __future__ import annotations

import time

import redis
import structlog
from redis.exceptions import RedisError

from .config import settings

logger = structlog.get_logger()

_KEY_PREFIX = "auth:revoked:"


class RedisTokenStore:
    def __init__(self, redis_url: str) -> None:
        self._client = redis.from_url(redis_url, decode_responses=True)

    def revoke(self, jti: str, ttl_seconds: int) -> None:
        self._client.set(f"{_KEY_PREFIX}{jti}", "1", ex=ttl_seconds)

    def consume(self, jti: str, ttl_seconds: int) -> bool:
        """Atomically mark the token as used; True only for the first caller."""
        return bool(self._client.set(f"{_KEY_PREFIX}{jti}", "1", ex=ttl_seconds, nx=True))

    def is_revoked(self, jti: str) -> bool:
        try:
            return bool(self._client.exists(f"{_KEY_PREFIX}{jti}"))
        except RedisError:
            # Fail open: an unavailable denylist must not lock users out.
            logger.exception("Token revocation store unavailable; failing open")
            return False


class InMemoryTokenStore:
    """Process-local denylist with TTLs, used by the test suite."""

    def __init__(self) -> None:
        self._revoked: dict[str, float] = {}

    def revoke(self, jti: str, ttl_seconds: int) -> None:
        self._revoked[jti] = time.time() + ttl_seconds

    def consume(self, jti: str, ttl_seconds: int) -> bool:
        if self.is_revoked(jti):
            return False
        self._revoked[jti] = time.time() + ttl_seconds
        return True

    def is_revoked(self, jti: str) -> bool:
        expiry = self._revoked.get(jti)
        if expiry is None:
            return False
        if time.time() > expiry:
            del self._revoked[jti]
            return False
        return True


_store: RedisTokenStore | InMemoryTokenStore = RedisTokenStore(settings.token_revocation_redis_url)


def revoke_token(jti: str, ttl_seconds: int) -> None:
    """Add ``jti`` to the denylist for ``ttl_seconds``."""
    _store.revoke(jti, ttl_seconds)


def consume_token(jti: str, ttl_seconds: int) -> bool:
    """Atomically consume (revoke) a token; True only for its first use."""
    return _store.consume(jti, ttl_seconds)


def is_token_revoked(jti: str) -> bool:
    """True if the token identified by ``jti`` has been revoked."""
    return _store.is_revoked(jti)


def reset_store_for_tests() -> None:
    """Point the store at the in-memory implementation (pytest only)."""
    global _store
    _store = InMemoryTokenStore()
