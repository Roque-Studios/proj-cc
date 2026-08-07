"""Public media endpoints: blurred previews and creator banners.

Two intentionally *public* (no-auth) endpoints:

- ``GET /preview/{post_id}/media?media_id={id}`` — the blurred ``PREVIEW``
  teaser of a post's media, shown on the landing page / feed to non-followers
  (and on locked paid broadcasts). The bytes are heavily blurred and stamped
  ``PREVIEW`` (``app.watermark.preview``), so nothing usable leaks while
  visitors still see the shape of the content. Hidden posts and media that
  doesn't belong to the post ``404`` exactly like the private endpoint.
- ``GET /media/banner/{key}`` — a creator's public hero banner (served to any
  visitor; the profile chrome is public by design, unlike the content).
- ``GET /media/avatar/{key}`` — a creator's public profile avatar.

All three deliberately live outside the authenticated ``/content`` router —
they are the *teaser* surfaces, never the real content.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..media import serve_preview, served_content_type
from ..models import Post, PostMedia
from ..storage import (
    MediaStorage,
    StorageError,
    get_avatar_storage,
    get_banner_storage,
    get_original_storage,
)

logger = structlog.get_logger()

router = APIRouter(tags=["public media"])

_PROFILE_IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@router.api_route("/preview/{post_id}/media", methods=["GET", "HEAD"])
def serve_media_preview(
    post_id: int,
    media_id: int = Query(..., description="Id of the media file within the post"),
    db: Session = Depends(get_db),
):
    """Serve the blurred public preview of a post's media (no auth).

    Available for any **visible** post to any visitor: the payload is the
    blurred ``PREVIEW`` transform, never the original. A hidden post, media
    that doesn't belong to the post, or a missing file all ``404`` — identical
    to the authenticated endpoint so ids can't be probed.
    """
    post = db.get(Post, post_id)
    if post is None or not post.is_visible:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )
    media = db.get(PostMedia, media_id)
    if media is None or media.post_id != post_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media not found",
        )
    if not get_original_storage().exists(media.storage_key):
        logger.warning(
            "Preview requested for a media row with a missing original file",
            post_id=post_id,
            media_id=media_id,
            storage_key=media.storage_key,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media file missing",
        )
    return Response(
        content=serve_preview(media.storage_key),
        media_type=served_content_type(media.media_type),
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _serve_profile_image(key: str, store: MediaStorage) -> Response:
    """Serve one stored public profile image with a correct content type."""
    try:
        data = store.read(key)
    except StorageError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found",
        )
    # The stored key carries the upload extension — serve the matching type
    # (JPEG is the fallback for anything unknown).
    media_type = _PROFILE_IMAGE_CONTENT_TYPES.get(Path(key).suffix.lower(), "image/jpeg")
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/media/banner/{key}")
def serve_creator_banner(key: str):
    """Serve a creator's public hero banner (any visitor)."""
    return _serve_profile_image(key, get_banner_storage())


@router.get("/media/avatar/{key}")
def serve_creator_avatar(key: str):
    """Serve a creator's public profile avatar (any visitor)."""
    return _serve_profile_image(key, get_avatar_storage())
