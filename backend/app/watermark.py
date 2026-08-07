"""Per-viewer image watermarking (traceable watermarks).

Renders the requesting viewer's identity + a timestamp into the image so a
leaked screenshot can be traced back to who fetched it and when. The watermark
is applied **on the fly** at serve time, because the identity of the requester
is only known then.

Properties:

- **Subtle**: a single line of small, semi-transparent text in the image's
  **bottom-right corner** (with a margin) — the content stays clean while
  every served copy still carries the viewer's traceable identity.
- **Legible**: white text with a dark outline and a drop shadow renders
  readably over arbitrary photo content without dominating it.
- **Format-preserving**: JPEG/PNG/WEBP are re-encoded in-kind; animated GIFs
  are rasterized to their first frame and served as PNG.
"""

from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Default fill/outline so the text stays readable on light and dark content.
_FILL = (255, 255, 255, 160)  # semi-transparent so the watermark stays subtle
_STROKE = (20, 20, 20, 180)
_STROKE_WIDTH_FACTOR = 0.08  # relative to font size
# Corner margin + drop-shadow offset (absolute px, so small images keep the
# watermark close to the corner instead of far inside it).
_CORNER_MARGIN = 10
_SHADOW_OFFSET = 2


def build_watermark_text(
    user_ref: str,
    timestamp: datetime,
    post_id: int | None = None,
) -> str:
    """The traceable text: short hashes of the viewer ref (+ post) and a UTC timestamp.

    ``{viewer_hash} {post_hash} {timestamp} UTC`` — both hashes are truncated
    sha256 prefixes so the text stays short while remaining traceable: the
    platform resolves them back to user/post ids by enumerating the sequential
    id spaces (see ``app.watermark_trace``). Without ``post_id`` the legacy
    3-field form ``{viewer_hash} {timestamp} UTC`` is produced (timestamp only
    identifies *when* a capture was served).
    """
    viewer_hash = hashlib.sha256(user_ref.encode()).hexdigest()[:10]
    fields = [viewer_hash]
    if post_id is not None:
        fields.append(hashlib.sha256(f"post:{post_id}".encode()).hexdigest()[:10])
    # ISO-8601 compact timestamp (no space) so the line splits cleanly and
    # parses back via datetime.fromisoformat.
    fields.append(f"{timestamp:%Y-%m-%dT%H:%M:%S} UTC")
    return " ".join(fields)


def _font_size(image_size: tuple[int, int]) -> int:
    """A small, subtle font — sized to the image but capped so very large
    photos don't get an oversized watermark."""
    width, height = image_size
    return min(max(14, min(width, height) // 32), 36)


def build_watermark_layer(
    image_size: tuple[int, int],
    user_ref: str,
    timestamp: datetime,
    image_bytes: bytes = b"",
    post_id: int | None = None,
) -> Image.Image:
    """A transparent RGBA layer with a small watermark in the bottom-right corner.

    A single line of small, semi-transparent traceable text (see
    :func:`build_watermark_text`) anchored to the image's bottom-right corner
    with a fixed margin — subtle enough not to spoil the photo, persistent
    enough to trace a leak back to its viewer and post. The text (not the
    placement) carries the viewer/post identity, so ``image_bytes`` is accepted
    only for signature compatibility and does not affect the layout.
    """
    width, height = image_size
    font_size = _font_size(image_size)
    font = ImageFont.load_default(size=font_size)
    text = build_watermark_text(user_ref, timestamp, post_id)

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]

    stroke_width = max(1, int(font_size * _STROKE_WIDTH_FACTOR))
    # Anchor the text at the bottom-right corner (right-aligned to the margin).
    x = width - text_w - _CORNER_MARGIN
    y = height - text_h - _CORNER_MARGIN

    # Drop shadow first, then the stroked text, for legibility.
    draw.text(
        (x + _SHADOW_OFFSET, y + _SHADOW_OFFSET),
        text,
        font=font,
        fill=(0, 0, 0, 120),
    )
    draw.text(
        (x, y),
        text,
        font=font,
        fill=_FILL,
        stroke_width=stroke_width,
        stroke_fill=_STROKE,
    )
    return layer


def _decode(image_bytes: bytes) -> tuple[Image.Image, str]:
    """Open the image and decide the output format (GIF -> PNG)."""
    image = Image.open(io.BytesIO(image_bytes))
    image.load()
    fmt = (image.format or "JPEG").upper()
    if fmt == "GIF":
        # Rasterize the first frame; animated GIFs lose animation when traced.
        image = image.convert("RGBA")
        fmt = "PNG"
    return image, fmt


def watermark(
    original: bytes,
    user_ref: str,
    timestamp: datetime | None = None,
    post_id: int | None = None,
) -> bytes:
    """Watermark ``original`` for the viewer identified by ``user_ref``.

    ``timestamp`` defaults to now (UTC) and is injectable for deterministic
    tests. ``post_id`` is embedded as a hash in the watermark text so a leak
    traces back to the post as well as the viewer (see ``app.watermark_trace``).
    The returned bytes are the re-encoded watermarked image — the original
    bytes themselves are never exposed.
    """
    timestamp = timestamp or datetime.now(timezone.utc)
    image, fmt = _decode(original)
    layer = build_watermark_layer(image.size, user_ref, timestamp, original, post_id)

    base = image.convert("RGBA")
    composited = Image.alpha_composite(base, layer)

    output = io.BytesIO()
    if fmt == "JPEG":
        composited.convert("RGB").save(output, format="JPEG", quality=88)
    elif fmt == "PNG":
        composited.save(output, format="PNG")
    elif fmt == "WEBP":
        composited.save(output, format="WEBP", quality=88)
    else:  # unknown formats fall back to JPEG
        composited.convert("RGB").save(output, format="JPEG", quality=88)
    return output.getvalue()


def output_format(original: bytes) -> str:
    """The content-type of :func:`watermark` output for a given input."""
    _, fmt = _decode(original)
    return {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }.get(fmt, "image/jpeg")


_PREVIEW_BLUR_RADIUS = 12
_PREVIEW_TEXT = "PREVIEW"


def build_preview_layer(image_size: tuple[int, int]) -> Image.Image:
    """A fixed diagonal ``PREVIEW`` stamp layer (public, no viewer identity)."""
    width, height = image_size
    font_size = max(28, min(width, height) // 6)
    font = ImageFont.load_default(size=font_size)
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    text_bbox = draw.textbbox((0, 0), _PREVIEW_TEXT, font=font)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]

    tile = Image.new("RGBA", (text_w + 16, text_h + 16), (0, 0, 0, 0))
    tile_draw = ImageDraw.Draw(tile)
    tile_draw.text(
        (8, 8),
        _PREVIEW_TEXT,
        font=font,
        fill=(255, 255, 255, 220),
        stroke_width=max(2, font_size // 10),
        stroke_fill=(20, 20, 20, 220),
    )
    tile = tile.rotate(-30, expand=True, resample=Image.BICUBIC)
    layer.paste(tile, (width // 2 - tile.width // 2, height // 2 - tile.height // 2), tile)
    return layer


def preview(original: bytes) -> bytes:
    """A blurred, ``PREVIEW``-stamped teaser of an original (public-safe).

    Non-followers see this on the landing page and feed: the real content is
    heavily blurred and stamped so nothing usable leaks, while visitors still
    get a sense of the post. Deterministic and viewer-independent (no identity
    watermark), so the same bytes serve every visitor and can be cached once
    per media file.
    """
    image, fmt = _decode(original)
    blurred = image.convert("RGB").filter(
        ImageFilter.GaussianBlur(_PREVIEW_BLUR_RADIUS)
    )
    composited = Image.alpha_composite(
        blurred.convert("RGBA"),
        build_preview_layer(image.size),
    )
    output = io.BytesIO()
    if fmt == "PNG":
        composited.save(output, format="PNG")
    elif fmt == "WEBP":
        composited.save(output, format="WEBP", quality=85)
    else:  # GIF is rasterized to PNG by _decode; everything else -> JPEG
        composited.convert("RGB").save(output, format="JPEG", quality=85)
    return output.getvalue()
