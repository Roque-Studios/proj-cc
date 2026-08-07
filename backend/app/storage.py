"""Storage abstraction for original media.

Originals (the unwatermarked uploads) live in a **private** store that is never
mounted or routed publicly: no FastAPI route reads it, no nginx location
proxies it. Only internal service code (``MediaStorage`` implementors and the
media pipeline) can read original bytes; clients only ever see the *rendered*
(watermarked) output served from the served-media store.

The abstraction mirrors ``PaymentProvider``: business code depends on the
interface, so the backing store (local disk today, S3/GCS tomorrow) can change
without touching callers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from .config import settings


class StorageError(Exception):
    """Base error for storage failures."""


class MediaStorage(ABC):
    """Keyed byte storage for original media. Keys are server-generated."""

    @abstractmethod
    def save(self, key: str, data: bytes) -> None:
        """Persist ``data`` under ``key`` (replacing any existing value)."""

    @abstractmethod
    def read(self, key: str) -> bytes:
        """Return the bytes stored under ``key``. Raises ``StorageError`` if absent."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove the value under ``key`` (no-op if absent)."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """True if a value is stored under ``key``."""


class DiskMediaStorage(MediaStorage):
    """Local-disk implementation of :class:`MediaStorage`.

    Keys are validated (no separators or ``..``) so a key can never escape the
    storage root — defense in depth, even though callers only pass server
    generated keys.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    # ------------------------------------------------------------------ #
    # Interface
    # ------------------------------------------------------------------ #

    def save(self, key: str, data: bytes) -> None:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read(self, key: str) -> bytes:
        path = self._path_for(key)
        if not path.is_file():
            raise StorageError(f"Media not found: {key}")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        self._path_for(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path_for(key).is_file()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _path_for(self, key: str) -> Path:
        """Resolve a key to an absolute path, rejecting traversal attempts."""
        if not key or "/" in key or "\\" in key or key in (".", ".."):
            raise StorageError(f"Invalid storage key: {key!r}")
        path = (self._root / key).resolve()
        if not str(path).startswith(str(self._root.resolve())):
            raise StorageError(f"Storage key escapes the root: {key!r}")
        return path


def get_original_storage() -> MediaStorage:
    """The app's original-media store (reads settings live for testability).

    Creating a fresh ``DiskMediaStorage`` per call is cheap (stateless), and
    reading ``settings.ORIGINAL_MEDIA_STORAGE_PATH`` at call time lets tests
    monkeypatch the path without cache invalidation.
    """
    return DiskMediaStorage(settings.ORIGINAL_MEDIA_STORAGE_PATH)


def get_banner_storage() -> MediaStorage:
    """The public banner store (creator hero images on the landing page).

    Unlike the private originals store, banner files are served directly to any
    visitor via ``GET /media/banner/{key}`` — banners are public by design (a
    blurred preview protects the *content*, not the profile chrome).
    """
    return DiskMediaStorage(settings.BANNER_STORAGE_PATH)


def get_avatar_storage() -> MediaStorage:
    """The public avatar store (creator profile pictures on the landing page).

    Like banners, avatars are public profile chrome served to any visitor via
    ``GET /media/avatar/{key}``.
    """
    return DiskMediaStorage(settings.AVATAR_STORAGE_PATH)
