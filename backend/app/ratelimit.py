"""Auth rate limiting: Redis-backed fixed-window throttling with fail-open.

Protects the unauthenticated / abuse-prone endpoints (register, login,
refresh, forgot/reset-password) against credential stuffing, account-creation
spam and reset-code abuse. Two scoping keys are used per endpoint:

- **per-IP** — applied via a FastAPI dependency (``rate_limit``), the bucket
  key includes the client IP resolved behind nginx (``X-Forwarded-For``); and
- **per-identity** (e.g. ``ip+email`` on login) — applied inline with
  ``check_rate_limit`` once the body is parsed (dependencies run before body
  parsing, so body-derived keys must be checked in the endpoint).

The window is a fixed window (``epoch // window`` in the key), enforced with a
single ``INCR`` + ``EXPIRE`` pair — atomic and one round-trip. The store is
**fail-open** (mirrors ``app.cache``): a Redis outage must never lock everyone
out of login — it degrades to no limiting and logs once.

``RateLimitStore`` is the swap-in interface; tests use
``InMemoryRateLimitStore`` (or a ``FakeRedis``-backed Redis store).
"""

from __future__ import annotations

import hashlib
import ipaddress
import time
from collections.abc import Callable

import redis
import structlog
from fastapi import Depends, HTTPException, Request, status
from redis.exceptions import RedisError

from .config import settings

logger = structlog.get_logger()

_KEY_PREFIX = "rl:"
_RETRY_AFTER = "Retry-After"

# Redis failures that must never break auth: connection/parse errors plus
# socket/DNS problems. Programming errors (wrong arity, type bugs) are NOT
# caught — they should surface loudly.
_STORE_FAILURES = (RedisError, OSError)

_denylist_logged = False


def _bucket_key(scope_key: str, window_seconds: int, now: float | None = None) -> str:
    """A fixed-window bucket key: the counter resets when the window rolls over."""
    t = int(now if now is not None else time.time())
    return f"{_KEY_PREFIX}{scope_key}:{int(t // window_seconds)}"


def email_scope_key(identity: str) -> str:
    """Hash an email so account identifiers never sit raw in Redis keys."""
    return "e:" + hashlib.sha256(identity.encode()).hexdigest()[:16]


class RateLimitStore:
    """Interface: shared counter + one-time-use primitives (see Redis impl)."""

    def hit(self, key: str, window_seconds: int, max_requests: int) -> tuple[bool, int]:
        """Record a hit. Returns ``(allowed, retry_after_seconds)``."""
        raise NotImplementedError

    def consume_once(self, key: str, ttl_seconds: int) -> bool:
        """Claim a single-use key (e.g. a PoW challenge). True = claimed now."""
        raise NotImplementedError


class RedisRateLimitStore(RateLimitStore):
    """Fixed-window counters via INCR + EXPIRE on the rate-limit Redis DB."""

    def __init__(self, redis_url: str) -> None:
        self._client = redis.from_url(redis_url, decode_responses=True)

    def hit(self, key: str, window_seconds: int, max_requests: int) -> tuple[bool, int]:
        try:
            count = self._client.incr(key)
            if count == 1:
                self._client.expire(key, window_seconds)
            if count > max_requests:
                ttl = self._client.ttl(key)
                return False, ttl if ttl and ttl > 0 else 1
            return True, 0
        except _STORE_FAILURES as exc:
            _mark_unavailable("hit", key, exc)
            return True, 0  # fail-open

    def consume_once(self, key: str, ttl_seconds: int) -> bool:
        try:
            return bool(self._client.set(key, "1", ex=ttl_seconds, nx=True))
        except _STORE_FAILURES as exc:
            _mark_unavailable("consume_once", key, exc)
            return True  # fail-open: can't prove reuse — the rate limiter still throttles


class InMemoryRateLimitStore(RateLimitStore):
    """Thread-safe-ish in-memory stand-in for tests (no Redis required)."""

    def __init__(self) -> None:
        # key -> [count, expires_at (monotonic)]
        self._store: dict[str, list[int | float]] = {}

    def hit(self, key: str, window_seconds: int, max_requests: int) -> tuple[bool, int]:
        now = time.monotonic()
        item = self._store.get(key)
        if item is None or item[1] <= now:
            self._store[key] = [1, now + window_seconds]
            return True, 0
        item[0] = int(item[0]) + 1
        return (True, 0) if int(item[0]) <= max_requests else (False, max(1, int(item[1] - now)))

    def consume_once(self, key: str, ttl_seconds: int) -> bool:
        now = time.monotonic()
        if key in self._store and self._store[key][1] > now:
            return False
        self._store[key] = [1, now + ttl_seconds]
        return True

    def clear(self) -> None:
        self._store.clear()


def _mark_unavailable(operation: str, key: str, exc: Exception) -> None:
    global _denylist_logged
    if not _denylist_logged:
        _denylist_logged = True
        logger.warning(
            "Rate-limit store unavailable — failing open",
            operation=operation,
            key=key,
            error=str(exc),
        )
    else:
        logger.debug("Rate-limit store unavailable (repeated)", error=str(exc))


# The process-wide store. Tests swap it for an InMemoryRateLimitStore.
_store: RateLimitStore = RedisRateLimitStore(settings.rate_limit_redis_url)


def _canonical_ip(raw: str) -> str | None:
    """Normalize an IP literal for keying, or ``None`` when it isn't an IP.

    Strips IPv4-mapped IPv6 (``::ffff:127.0.0.1`` → ``127.0.0.1``) and rejects
    junk — so a client can't rotate its identity by alternating textual
    representations of the same address.
    """
    try:
        addr = ipaddress.ip_address(raw.strip())
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return str(addr.ipv4_mapped)
    return str(addr)


def client_ip(request: Request) -> str:
    """The client's IP for rate limiting.

    Behind our own nginx the real client address is appended to
    ``X-Forwarded-For`` as the **last** entry (``proxy_add_x_forwarded_for``);
    trusting it is safe because nginx overwrites the header from the remote
    socket. When ``TRUST_PROXY_HEADERS`` is off (direct exposure) the socket
    address is used so spoofed headers can't inflate a quota.
    """
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            if parts:
                canonical = _canonical_ip(parts[-1])
                if canonical is not None:
                    return canonical
                # Last entry isn't a valid IP — treat the header as spoofed and
                # fall through to the socket peer rather than keying on junk.
    peer = request.client.host if request.client is not None else None
    if peer:
        return _canonical_ip(peer) or peer
    return "unknown"


def check_rate_limit(
    scope: str,
    identity: str,
    *,
    window_seconds: int,
    max_requests: int,
) -> None:
    """Inline limit check (raises 429) — for identity keys known only from the
    request body (e.g. ``ip+email`` on login). Fail-open on store errors."""
    key = _bucket_key(f"{scope}:{identity}", window_seconds)
    try:
        allowed, retry_after = _store.hit(key, window_seconds, max_requests)
    except _STORE_FAILURES as exc:
        _mark_unavailable("hit", key, exc)
        return  # fail-open: never lock users out because the limiter broke
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests — please try again later.",
            headers={_RETRY_AFTER: str(retry_after)},
        )


def consume_once(key: str, ttl_seconds: int) -> bool:
    """Claim a single-use key through the active store (e.g. a PoW challenge)."""
    try:
        return _store.consume_once(key, ttl_seconds)
    except _STORE_FAILURES as exc:
        _mark_unavailable("consume_once", key, exc)
        return True  # fail-open


def rate_limit(
    scope: str,
    *,
    window_seconds: int,
    max_requests: int,
    key_for: Callable[[Request], str] | None = None,
) -> Callable:
    """FastAPI dependency factory: per-IP fixed-window limit for ``scope``.

    ``key_for`` overrides the identity (default: the client IP). Webhook and
    media endpoints must not use this — signature-verified webhooks are the
    legitimate exception (provider IPs vary; retries are expected).
    """

    def dependency(request: Request) -> None:
        identity = key_for(request) if key_for is not None else client_ip(request)
        key = _bucket_key(f"{scope}:{identity}", window_seconds)
        try:
            allowed, retry_after = _store.hit(key, window_seconds, max_requests)
        except _STORE_FAILURES as exc:
            _mark_unavailable("hit", key, exc)
            return  # fail-open
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests — please try again later.",
                headers={_RETRY_AFTER: str(retry_after)},
            )

    return Depends(dependency)
