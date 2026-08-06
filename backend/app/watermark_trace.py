"""Watermark traceability: resolve a watermark's hashed identity to user/post.

The watermark text line embeds truncated sha256 prefixes of the viewer ref and
the post ref (see ``app.watermark.build_watermark_text``), e.g.::

    a1b2c3d4e5 f6a7b8c9d0 2026-08-06T12:00:00 UTC

The hashes are one-way, so decoding enumerates the sequential id spaces and
matches the prefix — O(max_id) sha256 calls, trivially fast for an
abuse-investigation tool (users and posts use auto-incrementing integer ids).
This is the same traceability tradeoff the watermark itself makes: the
truncated hash is public, but only the platform can enumerate the id space.

The enumeration bound is the highest id currently in the table, so a deleted
row is still resolvable while any later id keeps the bound above it; only
*tail* deletions (removing the highest ids) drop out of reach, which the
investigator sees as an unresolved post (null ``post_id``).

Consumed by the admin-gated ``GET /admin/watermark-trace`` endpoint
(``app.routers.admin``). The legacy 3-field format (no post hash) is accepted
for watermarks rendered before the post identity was embedded.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Post, User

_HASH_LEN = 10
_HASH_RE = re.compile(r"^[0-9a-f]{10}$")


class WatermarkTraceError(ValueError):
    """Raised when a watermark text line is malformed and can't be parsed."""


@dataclass(frozen=True)
class WatermarkToken:
    """The parsed fields of a watermark text line."""

    viewer_hash: str
    post_hash: str | None  # None for legacy watermarks (pre post-identity)
    fetched_at: datetime | None  # capture time embedded in the watermark (naive UTC)


def parse_watermark_text(text: str) -> WatermarkToken:
    """Parse a watermark text line into its fields.

    Accepts both the current 4-field form ``{viewer} {post} {when} UTC`` and
    the legacy 3-field form ``{viewer} {when} UTC``. Raises
    :class:`WatermarkTraceError` for anything malformed.
    """
    if not text:
        raise WatermarkTraceError("Watermark text is empty")
    fields = text.strip().split()
    if len(fields) == 4:
        viewer_hash, post_hash, when, tz = fields
    elif len(fields) == 3:
        viewer_hash, when, tz = fields
        post_hash = None
    else:
        raise WatermarkTraceError(
            f"Expected 3 or 4 space-separated fields, got {len(fields)}"
        )
    if not _HASH_RE.match(viewer_hash):
        raise WatermarkTraceError(f"Invalid viewer hash: {viewer_hash!r}")
    if post_hash is not None and not _HASH_RE.match(post_hash):
        raise WatermarkTraceError(f"Invalid post hash: {post_hash!r}")
    if tz != "UTC":
        raise WatermarkTraceError(f"Expected UTC timezone marker, got {tz!r}")
    try:
        fetched_at = datetime.fromisoformat(when)
    except ValueError:
        raise WatermarkTraceError(f"Invalid timestamp: {when!r}")
    return WatermarkToken(
        viewer_hash=viewer_hash,
        post_hash=post_hash,
        fetched_at=fetched_at,
    )


def _max_id(db: Session, model) -> int:
    """Highest id currently in the table (0 when empty) — the enumeration bound."""
    return db.scalar(select(func.max(model.id))) or 0


def _matching_ids(db: Session, model, prefix: str, target_hash: str) -> list[int]:
    """Ids whose truncated sha256(prefix + id) equals ``target_hash``."""
    return [
        entity_id
        for entity_id in range(1, _max_id(db, model) + 1)
        if hashlib.sha256(f"{prefix}{entity_id}".encode()).hexdigest()[:_HASH_LEN]
        == target_hash
    ]


@dataclass(frozen=True)
class TraceResult:
    """The resolved origin of a watermark: the viewer (user) and the post."""

    viewer_hash: str
    post_hash: str | None
    fetched_at: datetime | None
    user_id: int | None  # None when no user matches the viewer hash
    user_email: str | None  # None when the matching user row is gone
    user_matches: int  # ids matching the viewer hash (1 normally; >1 = hash collision)
    post_id: int | None  # None for legacy watermarks / unknown / deleted post
    post_caption: str | None
    post_matches: int  # ids matching the post hash (0 = no post identity present)


def lookup_trace(db: Session, text: str) -> TraceResult:
    """Resolve a watermark text line to its originating user and post.

    The user resolves from the viewer hash; the post resolves from the post
    hash when present (legacy watermarks carry none). The *first* matching id
    is reported per hash, with ``user_matches``/``post_matches`` exposing how
    many ids matched so a truncated-hash collision (exponentially unlikely at
    40 bits) stays visible to the investigator. A deleted row still resolves
    while any later id keeps the enumeration bound above it (the email/caption
    are null then); only *tail* deletions (removing the highest ids) drop out
    of reach and report no match.
    """
    token = parse_watermark_text(text)

    user_ids = _matching_ids(db, User, "user:", token.viewer_hash)
    user_id = user_ids[0] if user_ids else None
    user = db.get(User, user_id) if user_id is not None else None

    post_ids = _matching_ids(db, Post, "post:", token.post_hash) if token.post_hash else []
    post_id = post_ids[0] if post_ids else None
    post = db.get(Post, post_id) if post_id is not None else None

    return TraceResult(
        viewer_hash=token.viewer_hash,
        post_hash=token.post_hash,
        fetched_at=token.fetched_at,
        user_id=user_id,
        user_email=user.email if user else None,
        user_matches=len(user_ids),
        post_id=post_id,
        post_caption=post.caption if post else None,
        post_matches=len(post_ids),
    )
