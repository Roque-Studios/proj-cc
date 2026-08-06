"""Unit tests for the per-viewer image watermarking service.

Acceptance: the output image contains a visibly correct, legible watermark;
the same input always produces deterministic placement; and the watermark text
decodes back to the correct user pairing (verified by reconstructing the exact
output from a given (image, user, timestamp) — only the correct pairing
reproduces it).
"""

from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone

from PIL import Image

from app.media import render_served_media, served_content_type
from app.watermark import (
    build_watermark_layer,
    build_watermark_text,
    output_format,
    watermark,
)

TS = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)


def _png(width: int = 320, height: int = 240) -> bytes:
    img = Image.new("RGB", (width, height), (128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(width: int = 320, height: int = 240) -> bytes:
    img = Image.new("RGB", (width, height), (128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _gif() -> bytes:
    img = Image.new("RGB", (64, 64), (10, 200, 30))
    buf = io.BytesIO()
    img.save(buf, format="GIF")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Watermark text
# --------------------------------------------------------------------------- #

def test_watermark_text_contains_hashed_user_and_timestamp():
    text = build_watermark_text("user:42", TS)
    token, when, tz = text.split(" ")
    assert token == hashlib.sha256(b"user:42").hexdigest()[:10]
    assert when == "2026-08-06T12:00:00"
    assert datetime.fromisoformat(when).tzinfo is None  # parseable back
    assert tz == "UTC"
    # Different viewers get different hashes.
    other = build_watermark_text("user:7", TS).split(" ")[0]
    assert other == hashlib.sha256(b"user:7").hexdigest()[:10]
    assert other != token


def test_watermark_text_embeds_post_hash_when_given():
    """With a post id the text carries a second hash (leak -> post traceability)."""
    text = build_watermark_text("user:42", TS, post_id=7)
    token, post_hash, when, tz = text.split(" ")
    assert token == hashlib.sha256(b"user:42").hexdigest()[:10]
    assert post_hash == hashlib.sha256(b"post:7").hexdigest()[:10]
    assert when == "2026-08-06T12:00:00"
    assert tz == "UTC"
    # The same viewer without a post id stays 3 fields (legacy form).
    assert len(build_watermark_text("user:42", TS).split(" ")) == 3


# --------------------------------------------------------------------------- #
# Determinism and traceability
# --------------------------------------------------------------------------- #

def test_watermark_is_byte_identical_for_same_inputs():
    img = _png()
    assert watermark(img, "user:1", TS) == watermark(img, "user:1", TS)


def test_watermark_differs_across_viewers_and_timestamps():
    img = _png()
    base = watermark(img, "user:1", TS)
    assert watermark(img, "user:2", TS) != base  # different placement + text
    later = TS.replace(hour=13)
    assert watermark(img, "user:1", later) != base  # timestamp changed


def test_watermark_actually_changes_the_image():
    img = _png()
    assert watermark(img, "user:1", TS) != img


# --------------------------------------------------------------------------- #
# Decode-back pairing (reconstruction match, no OCR dependency)
# --------------------------------------------------------------------------- #

def test_watermark_decodes_back_to_correct_pairing():
    img = _png()
    out = watermark(img, "user:42", TS)

    # The correct (image, user, timestamp) pairing reproduces the exact output.
    source = Image.open(io.BytesIO(img)).convert("RGBA")
    layer = build_watermark_layer(source.size, "user:42", TS, img)
    expected = Image.alpha_composite(source, layer)
    buf = io.BytesIO()
    expected.save(buf, format="PNG")
    assert out == buf.getvalue()

    # A different viewer cannot reproduce this output.
    wrong_layer = build_watermark_layer(source.size, "user:7", TS, img)
    wrong = Image.alpha_composite(source, wrong_layer)
    buf2 = io.BytesIO()
    wrong.save(buf2, format="PNG")
    assert out != buf2.getvalue()


# --------------------------------------------------------------------------- #
# Legibility
# --------------------------------------------------------------------------- #

def test_watermark_layer_has_legible_ink():
    width, height = 320, 240
    layer = build_watermark_layer((width, height), "user:1", TS)

    data = layer.getdata()  # RGBA tuples
    ink = sum(1 for r, g, b, a in data if a > 0)
    # Tiled diagonal text at font size ~22 covers thousands of pixels.
    assert ink > 2000

    # Legible on any background: bright fill AND dark outline both present.
    bright = sum(1 for r, g, b, a in data if a > 0 and r > 200 and g > 200 and b > 200)
    dark = sum(1 for r, g, b, a in data if a > 0 and r < 80 and g < 80 and b < 80)
    assert bright > 100
    assert dark > 100


# --------------------------------------------------------------------------- #
# Format handling
# --------------------------------------------------------------------------- #

def test_format_preserved_jpeg_png_webp():
    assert watermark(_jpeg(), "user:1", TS)[:3] == b"\xff\xd8\xff"
    assert watermark(_png(), "user:1", TS)[:8] == b"\x89PNG\r\n\x1a\n"
    assert output_format(_jpeg()) == "image/jpeg"
    assert output_format(_png()) == "image/png"


def test_gif_rasterized_to_png():
    assert output_format(_gif()) == "image/png"
    assert watermark(_gif(), "user:1", TS)[:8] == b"\x89PNG\r\n\x1a\n"


def test_served_content_type_matches_output_format_without_decoding():
    """The DB-derived content type matches output_format for every format."""
    assert served_content_type("image/jpeg") == output_format(_jpeg())
    assert served_content_type("image/png") == output_format(_png())
    assert served_content_type("image/webp") == "image/webp"
    assert served_content_type("image/gif") == output_format(_gif())  # -> png
    assert served_content_type(None) == "image/jpeg"  # defensive fallback


# --------------------------------------------------------------------------- #
# render_served_media wiring
# --------------------------------------------------------------------------- #

def test_render_served_media_watermarks_per_viewer():
    original = _png()
    served = render_served_media(original, "user:9", TS)
    assert served != original
    assert served == render_served_media(original, "user:9", TS)
    assert served != render_served_media(original, "user:8", TS)
