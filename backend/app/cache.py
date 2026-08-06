"""Redis-backed cache layer for watermarked media.

Watermarked output is **viewer-specific** — it carries the requesting viewer's
identity — so cached entries are keyed by *both* the viewer ref and the media
id: ``watermarked:media:{user_ref}:{media_id}``. Repeating the same
(viewer, media) pair within the TTL window is served from Redis instead of
re-rendering, which matters most for large/video media.

- **TTL**: entries expire after ``Settings.WATERMARK_CACHE_TTL_SECONDS`` (or the
  ``ttl_seconds`` override) and Redis evicts them automatically. Note that the
  cached bytes carry the *first render's* timestamp, so watermark staleness is
  bounded by the TTL — acceptable, since the viewer identity (the traceable
  part) is what varies per user.
- **Best-effort**: every call is resilient — a Redis outage degrades to a cache
  miss (the caller renders and serves) rather than failing the media request.
"""

from __future__ import annotations

from typing import Optional

import redis
import structlog

from .config import settings

logger = structlog.get_logger()

_KEY_PREFIX = "watermarked:media:"

_client: Optional[redis.Redis] = None

# Once an outage has been logged we stay quiet (debug) until the cache works
# again — a sustained Redis failure shouldn't spam a warning per media request.
_unavailable_logged = False

# Redis failures that must never break media serving: connection/parse errors
# (RedisError) plus socket/DNS problems (OSError). Programming errors (wrong
# arity, type bugs) are NOT caught — they should surface loudly.
_CACHE_FAILURES = (redis.RedisError, OSError)


def _mark_unavailable(operation: str, key: str, exc: Exception) -> None:
    global _unavailable_logged
    if not _unavailable_logged:
        _unavailable_logged = True
        logger.warning(
            "Watermark cache unavailable", operation=operation, key=key, error=str(exc)
        )
    else:
        logger.debug(
            "Watermark cache unavailable (repeated)", operation=operation, key=key
        )


def _mark_available() -> None:
    global _unavailable_logged
    _unavailable_logged = False


def _get_client() -> redis.Redis:
    """Lazily create and reuse a single Redis client for the cache."""
    global _client
    if _client is None:
        _client = redis.from_url(
            settings.watermark_cache_redis_url,
            decode_responses=False,
        )
        logger.info(
            "Watermarked media cache client created",
            url=settings.watermark_cache_redis_url,
        )
    return _client


def cache_key(user_ref: str, media_id: str) -> str:
    """Namespaced key for a (viewer, media) pair — per-viewer watermark bytes."""
    return f"{_KEY_PREFIX}{user_ref}:{media_id}"


def get_cached_watermarked_media(user_ref: str, media_id: str) -> Optional[bytes]:
    """Return the cached watermarked bytes for a viewer+media, or None on miss.

    Best-effort: any Redis failure is treated as a miss (never raises).
    """
    key = cache_key(user_ref, media_id)
    try:
        data = _get_client().get(key)
    except _CACHE_FAILURES as exc:
        _mark_unavailable("get", key, exc)
        return None
    _mark_available()
    if data is None:
        logger.debug("Watermark cache miss", user_ref=user_ref, media_id=media_id, key=key)
    else:
        logger.debug("Watermark cache hit", user_ref=user_ref, media_id=media_id, key=key)
    return data


def set_watermarked_media(
    user_ref: str,
    media_id: str,
    data: bytes,
    ttl_seconds: Optional[int] = None,
) -> None:
    """Cache watermarked bytes for a viewer+media with the configured TTL.

    ``ttl_seconds`` overrides ``Settings.WATERMARK_CACHE_TTL_SECONDS`` when given.
    Best-effort: a Redis failure is logged and skipped (never raises).
    """
    ttl = ttl_seconds if ttl_seconds is not None else settings.WATERMARK_CACHE_TTL_SECONDS
    key = cache_key(user_ref, media_id)
    try:
        _get_client().set(key, data, ex=ttl)
    except _CACHE_FAILURES as exc:
        _mark_unavailable("set", key, exc)
        return
    _mark_available()
    logger.debug(
        "Watermark media cached",
        user_ref=user_ref,
        media_id=media_id,
        key=key,
        bytes=len(data),
        ttl_seconds=ttl,
    )


def delete_watermarked_media(user_ref: str, media_id: str) -> None:
    """Invalidate a cached (viewer, media) entry. Best-effort (never raises)."""
    key = cache_key(user_ref, media_id)
    try:
        _get_client().delete(key)
    except _CACHE_FAILURES as exc:
        _mark_unavailable("delete", key, exc)
        return
    _mark_available()
    logger.debug("Watermark cache entry deleted", user_ref=user_ref, media_id=media_id, key=key)


def get_cached_media_ttl(user_ref: str, media_id: str) -> int:
    """Remaining TTL in seconds (-2 = key missing, -1 = no expiry). Best-effort."""
    key = cache_key(user_ref, media_id)
    try:
        result = _get_client().ttl(key)
    except _CACHE_FAILURES as exc:
        _mark_unavailable("ttl", key, exc)
        return -2
    _mark_available()
    return result
