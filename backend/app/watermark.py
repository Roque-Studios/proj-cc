"""Per-viewer image watermarking (OnlyFans-style traceable watermarks).

Renders the requesting viewer's identity + a timestamp into the image so a
leaked screenshot can be traced back to who fetched it and when. The watermark
is applied **on the fly** at serve time, because the identity of the requester
is only known then.

Properties:

- **Deterministic placement**: the layout is seeded from a hash of the image
  bytes + the viewer reference, so the same (image, viewer) always produces
  the same placement — while different viewers see different layouts.
- **Legible**: white text with a dark outline and a subtle shadow band renders
  readably over arbitrary photo content.
- **Format-preserving**: JPEG/PNG/WEBP are re-encoded in-kind; animated GIFs
  are rasterized to their first frame and served as PNG.
"""

from __future__ import annotations

import hashlib
import io
import random
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Default fill/outline so the text stays readable on light and dark content.
_FILL = (255, 255, 255, 210)
_STROKE = (20, 20, 20, 210)
_STROKE_WIDTH_FACTOR = 0.06  # relative to font size
_SHADOW_OFFSET = 0.02  # relative to image size


def _seed(image_bytes: bytes, user_ref: str) -> int:
    """Deterministic PRNG seed for a given (image, viewer) pair."""
    return int.from_bytes(
        hashlib.sha256(image_bytes + user_ref.encode()).digest()[:8], "big"
    )


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
    width, height = image_size
    return max(22, min(width, height) // 12)


def build_watermark_layer(
    image_size: tuple[int, int],
    user_ref: str,
    timestamp: datetime,
    image_bytes: bytes = b"",
    post_id: int | None = None,
) -> Image.Image:
    """A transparent RGBA layer with the watermark tiled diagonally across it.

    All randomness is seeded from ``(image_bytes, user_ref)`` — same pair, same
    layout; different viewer, different placement. ``image_bytes`` is optional
    (used only for seeding); when omitted the layout is deterministic per
    ``(size, user_ref)``. ``post_id`` is embedded as a hash in the text (see
    :func:`build_watermark_text`) so a leak can be traced back to the post.
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

    rng = random.Random(_seed(image_bytes or f"{width}x{height}".encode(), user_ref))
    angle = rng.uniform(-45, -20)
    stroke_width = max(1, int(font_size * _STROKE_WIDTH_FACTOR))
    shadow = int(min(width, height) * _SHADOW_OFFSET)

    def _draw_tile(px: int, py: int) -> None:
        # Roomy tile so the shadow + stroked text + rotation all stay inside.
        tile_w = text_w + 2 * shadow + 8
        tile_h = text_h + 2 * shadow + 8
        tile = Image.new("RGBA", (tile_w, tile_h), (0, 0, 0, 0))
        tile_draw = ImageDraw.Draw(tile)
        # Drop shadow first, then the stroked text, for legibility.
        tile_draw.text(
            (4 + shadow, 4 + shadow), text, font=font, fill=(0, 0, 0, 120)
        )
        tile_draw.text(
            (4, 4),
            text,
            font=font,
            fill=_FILL,
            stroke_width=stroke_width,
            stroke_fill=_STROKE,
        )
        # expand=True keeps the whole rotated line (a thin unexpanded tile would
        # clip most of the diagonal text to nothing).
        tile = tile.rotate(angle, expand=True, resample=Image.BICUBIC)
        layer.paste(tile, (px, py), tile)

    # Staggered brick grid: rows walk across, alternate rows offset by half a
    # column, and every tile rotated — reads as a diagonal watermark, dense
    # enough that cropping it out is impractical.
    row_h = int(max(text_h, 14) * rng.uniform(2.0, 3.2))
    col_w = int(max(text_w, text_h) * rng.uniform(1.15, 1.45))
    row = 0
    y = -max(text_h * 2, 32)
    while y < height:
        offset = (row % 2) * (col_w // 2)
        x = -max(text_w, text_h) * 2 + offset
        while x < width:
            _draw_tile(int(x), int(y))
            x += col_w
        y += row_h
        row += 1

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
