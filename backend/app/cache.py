"""Redis-backed cache layer for watermarked media.

Watermarked media binaries are stored under namespaced keys with a configurable
TTL (``Settings.WATERMARK_CACHE_TTL_SECONDS``) so Redis evicts stale entries
automatically. Intended to be used by the media watermarking pipeline (TBD).
"""

from __future__ import annotations

from typing import Optional

import redis
import structlog

from .config import settings

logger = structlog.get_logger()

_KEY_PREFIX = "watermarked:media:"

_client: Optional[redis.Redis] = None


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


def cache_key(media_id: str) -> str:
    """Namespaced key for a given media identifier."""
    return f"{_KEY_PREFIX}{media_id}"


def get_cached_watermarked_media(media_id: str) -> Optional[bytes]:
    """Return the cached watermarked media bytes, or None on a miss/expiry."""
    key = cache_key(media_id)
    data = _get_client().get(key)
    if data is None:
        logger.debug("Watermarked media cache miss", media_id=media_id, key=key)
    else:
        logger.debug("Watermarked media cache hit", media_id=media_id, key=key)
    return data


def set_watermarked_media(
    media_id: str,
    data: bytes,
    ttl_seconds: Optional[int] = None,
) -> None:
    """Cache watermarked media bytes with the configured TTL.

    ``ttl_seconds`` overrides ``Settings.WATERMARK_CACHE_TTL_SECONDS`` when given.
    """
    ttl = ttl_seconds if ttl_seconds is not None else settings.WATERMARK_CACHE_TTL_SECONDS
    key = cache_key(media_id)
    _get_client().set(key, data, ex=ttl)
    logger.info(
        "Watermarked media cached",
        media_id=media_id,
        key=key,
        bytes=len(data),
        ttl_seconds=ttl,
    )


def delete_watermarked_media(media_id: str) -> None:
    """Invalidate a cached watermarked media entry."""
    key = cache_key(media_id)
    _get_client().delete(key)
    logger.info("Watermarked media cache entry deleted", media_id=media_id, key=key)


def get_cached_media_ttl(media_id: str) -> int:
    """Remaining TTL in seconds (-2 = key missing, -1 = no expiry)."""
    return _get_client().ttl(cache_key(media_id))
