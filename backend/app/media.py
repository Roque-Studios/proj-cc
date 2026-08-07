"""Photo-post media: upload validation and the serve-time rendering seam.

Validation is defense-in-depth: the client-declared extension and content type
are checked, but the **magic bytes** are the authority (a client can spoof the
``Content-Type`` header).

Storage model:

- **originals** (the unwatermarked uploads) live in the private store in
  ``app.storage`` — never served to clients, readable only by internal code;
- what ``GET /content/{post_id}/media?media_id={id}`` returns (see
  ``app.routers.content``) is the original **watermarked on the fly** for the
  requesting viewer via :func:`render_served_media` (see ``app.watermark``),
  after the viewer passes the follower/owner authorization check. No
  pre-rendered served copy is persisted.
"""

from __future__ import annotations

import io
import time
from datetime import datetime
from pathlib import Path

import structlog
from PIL import Image

from .cache import get_cached_watermarked_media, set_watermarked_media
from .config import settings
from .storage import get_original_storage
from .watermark import preview, watermark

logger = structlog.get_logger()

# Canonical extension -> expected sniffed type (used to catch extension/content
# mismatches, e.g. a .png file containing JPEG bytes).
_EXT_TO_TYPE = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class MediaValidationError(Exception):
    """Raised when an upload fails type/size validation."""


def _sniff_image_type(data: bytes) -> str | None:
    """Detect the image type from magic bytes (spoof-proof), or None."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_upload(filename: str, content_type: str, data: bytes) -> str:
    """Validate an uploaded photo; returns the authoritative media type.

    Raises ``MediaValidationError`` (mapped to a 400 by the endpoint) when the
    extension, declared content type, or actual file content is not a supported
    image.
    """
    ext = Path(filename or "").suffix.lower()
    if ext not in settings.allowed_media_extensions:
        allowed = ", ".join(sorted(settings.allowed_media_extensions))
        raise MediaValidationError(
            f"Unsupported file type: {ext or '(no extension)'}. Allowed: {allowed}"
        )
    if not content_type.startswith("image/"):
        raise MediaValidationError("Only image files are allowed")
    sniffed = _sniff_image_type(data)
    if sniffed is None:
        raise MediaValidationError("File content is not a valid image")
    expected = _EXT_TO_TYPE.get(ext)
    if expected is not None and sniffed != expected:
        raise MediaValidationError(
            f"File content ({sniffed}) does not match its extension ({ext})"
        )
    # The image must actually decode — magic bytes alone can be spoofed, and the
    # watermarking pipeline re-encodes every image on each serve, so a header-
    # only or truncated upload would 500 later instead of 400 now.
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
    except Exception as exc:  # UnidentifiedImageError / truncated-image OSError
        raise MediaValidationError("File content is not a valid image") from exc
    return sniffed


def save_original(data: bytes, storage_key: str) -> None:
    """Persist the unwatermarked original in the private store.

    Originals are never served: no route or proxy reads this store — only
    internal service code (e.g. the watermarking pipeline) can retrieve them.
    """
    get_original_storage().save(storage_key, data)


def delete_original(storage_key: str) -> None:
    """Remove an original from the private store (no-op if absent)."""
    get_original_storage().delete(storage_key)


def serve_preview(storage_key: str) -> bytes:
    """The blurred public preview bytes for a media file (cached, best-effort).

    Unlike the per-viewer watermarked bytes, a preview is viewer-independent
    (blur + ``PREVIEW`` stamp, no identity), so it is cached under the fixed
    ``preview`` ref and served to any visitor. A cache failure degrades to a
    fresh render.
    """
    cached = get_cached_watermarked_media("preview", storage_key)
    if cached is not None:
        return cached
    blurred = preview(get_original_storage().read(storage_key))
    set_watermarked_media("preview", storage_key, blurred)
    return blurred


def served_content_type(media_type: str | None) -> str:
    """Content-type served for a stored original, derived without decoding it.

    Matches ``app.watermark.output_format`` for every validated upload: animated
    GIFs are rasterized to PNG on serve (see ``app.watermark``); every other
    format is served in-kind. Deriving this from the stored ``PostMedia.media_type``
    (rather than re-opening the file) keeps the media hot path free of image
    decodes — important when serving cached watermark hits.
    """
    if media_type == "image/gif":
        return "image/png"
    return media_type or "image/jpeg"


def render_served_media(
    original: bytes,
    user_ref: str,
    timestamp: datetime | None = None,
    post_id: int | None = None,
) -> bytes:
    """Render the client-facing bytes of an original for a specific viewer.

    Watermarks the original on the fly with the requesting viewer's identity
    (hashed user ref + timestamp) and the post it belongs to (``post_id`` is
    embedded as a hash for leak traceability, see ``app.watermark_trace``).
    The returned bytes are a transformation of the original — the original
    bytes themselves are never served. ``timestamp`` defaults to now and is
    injectable for deterministic tests.
    """
    return watermark(original, user_ref, timestamp, post_id)


def serve_media(
    storage_key: str,
    user_ref: str,
    post_id: int | None = None,
) -> tuple[bytes, str]:
    """The client-facing bytes for a (media, viewer) pair: cached or rendered.

    Returns ``(bytes, cache_status)`` where ``cache_status`` is ``"hit"`` or
    ``"miss"``. The private original is read from storage **only on a cache
    miss** (the second request for the same viewer skips the disk read and the
    image decode entirely). The returned bytes are always the watermarked
    transform — the original bytes are never returned by this function.
    ``post_id`` is embedded in the watermark text for leak traceability (it is
    constant per (viewer, media), so per-viewer cache determinism is preserved).

    The cache is best-effort: a Redis outage degrades to a miss (render and
    serve) rather than failing the media request.
    """
    cached = get_cached_watermarked_media(user_ref, storage_key)
    if cached is not None:
        return cached, "hit"
    original = get_original_storage().read(storage_key)
    start = time.perf_counter()
    watermarked = render_served_media(original, user_ref, post_id=post_id)
    render_ms = round((time.perf_counter() - start) * 1000, 1)
    set_watermarked_media(user_ref, storage_key, watermarked)
    logger.info(
        "watermark_cache_miss",
        user_ref=user_ref,
        media_id=storage_key,
        render_ms=render_ms,
    )
    return watermarked, "miss"
