"""Post endpoints: creators upload photo posts (follower-only content).

``POST /posts`` accepts a multipart form with an optional caption and one or
more image files. Only a user with the ``creator`` role may create posts (403
otherwise). Every file is validated (extension, declared content type, magic
bytes, size) before anything is persisted, so a failed validation leaves no
rows behind.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import require_creator
from ..media import (
    MediaValidationError,
    delete_original,
    save_original,
    validate_upload,
)
from ..models import Post, PostMedia, User
from ..schemas import PostOut, build_post_out

router = APIRouter(prefix="/posts", tags=["posts"])

_CHUNK_SIZE = 64 * 1024


def _read_with_limit(upload: UploadFile, limit: int) -> bytes:
    """Read an upload fully, rejecting it once it exceeds ``limit`` bytes.

    Reading in chunks keeps memory bounded for huge/abusive uploads — the file
    is rejected as soon as the limit is crossed, not after buffering it all.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = upload.file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"File exceeds the {limit} byte size limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", response_model=PostOut, status_code=status.HTTP_201_CREATED)
def create_post(
    caption: str | None = Form(default=None, max_length=2000),
    price_cents: int | None = Form(
        default=None,
        ge=1,
        le=100_000,
        description=(
            "Optional one-time unlock price in cents — when set, this post is "
            "a paid broadcast: subscribers see a locked preview until they pay."
        ),
    ),
    files: list[UploadFile] | None = File(default=None),
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: upload a photo post (follower-only content).

    Requires at least one image; returns the created post with its media
    (each with an auth-gated ``/content/{post_id}/media?media_id={id}`` url).
    With ``price_cents`` set the post becomes a **paid broadcast**: it is
    delivered to all subscribers as a locked preview and each subscriber pays
    the one-time price to unlock full media access (see
    ``app.services.broadcasts``). All files are validated before any row or
    file is written.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one media file is required",
        )
    if caption is not None and not caption.strip():
        caption = None

    # Validate everything up front — nothing is persisted on failure.
    prepared: list[tuple[bytes, str, str]] = []  # (data, media_type, ext)
    for upload in files:
        data = _read_with_limit(upload, settings.MAX_MEDIA_SIZE_BYTES)
        try:
            media_type = validate_upload(
                upload.filename or "",
                upload.content_type or "",
                data,
            )
        except MediaValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )
        ext = Path(upload.filename or "").suffix.lower()
        prepared.append((data, media_type, ext))

    post = Post(creator_id=user.id, caption=caption, broadcast_price_cents=price_cents)
    db.add(post)
    db.flush()  # assign post.id without committing yet

    # If the DB commit fails, clean up the originals we already wrote so a
    # failed upload never leaves orphaned bytes behind.
    written_keys: list[str] = []
    try:
        for original, media_type, ext in prepared:
            storage_key = f"{uuid.uuid4().hex}{ext}"
            # The unwatermarked original goes to the private store — it is
            # never served to clients. Serving watermarks it on the fly per
            # viewer (see media.render_served_media), so no copy is rendered
            # or persisted here.
            save_original(original, storage_key)
            written_keys.append(storage_key)
            db.add(
                PostMedia(
                    post_id=post.id,
                    media_type=media_type,
                    storage_key=storage_key,
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        for storage_key in written_keys:
            delete_original(storage_key)
        raise

    db.refresh(post)
    # The creator owns the post — always unlocked (``unlocked`` is None for
    # regular posts, True for their own paid broadcasts).
    return build_post_out(
        post,
        unlocked=True if post.broadcast_price_cents is not None else None,
        include_media_urls=True,
    )
