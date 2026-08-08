"""24-hour story endpoints.

Stories are the ephemeral counterpart to posts: a creator uploads one or more
images, they live for exactly 24 hours (``expires_at``), and they are visible
**only to active followers** (or the creator themselves) — never to anonymous
or registered-but-unsubscribed viewers, so the public ``has_active_story``
badge is the only signal non-followers get.

Endpoints:

- ``POST /stories`` — creator-only multipart upload (caption + images). Every
  file is validated before anything is persisted, mirroring ``POST /posts``.
- ``GET /stories/{creator_id}`` — the creator's *active* (unexpired) stories;
  follower-only (or the creator's own view).
- ``GET /stories/{story_id}/media`` — auth-gated, watermarked media serving,
  identical in spirit to ``/content/{post_id}/media``.
- ``GET /creator/stories`` — the creator's dashboard list (expired stories
  included, so the UI can show what auto-expired).
- ``DELETE /stories/{story_id}`` — creator deletes their own story.

Expiry is enforced on every read path (listing + media), and the shared query
helpers live in ``app.services.stories`` so the router and the landing builder
agree on what "an active story" means.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from ..access import resolve_viewer_context
from ..config import settings
from ..database import get_db
from ..deps import require_creator
from ..media import (
    MediaValidationError,
    delete_original,
    save_original,
    serve_media,
    served_content_type,
    validate_upload,
)
from ..models import Story, StoryMedia, User, UserRole
from ..schemas import StoryMediaOut, StoryOut
from ..services.stories import STORY_TTL, StoryService
from ..storage import get_original_storage

# Follower/creator-facing story endpoints (``/stories/...``).
router = APIRouter(prefix="/stories", tags=["stories"])
# Creator dashboard endpoints live on their own prefix so ``/creator/stories``
# never collides with ``/stories/{creator_id}``.
dashboard_router = APIRouter(prefix="/creator/stories", tags=["creator"])

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


def _story_out(story: Story) -> StoryOut:
    """Dashboard/viewer shape for one story — media urls always included
    (every story endpoint is owner- or follower-gated)."""
    return StoryOut(
        id=story.id,
        creator_id=story.creator_id,
        caption=story.caption,
        expires_at=story.expires_at,
        created_at=story.created_at,
        media=[
            StoryMediaOut(
                id=media.id,
                media_type=media.media_type,
                media_url=media.media_url,
                created_at=media.created_at,
            )
            for media in story.media
        ],
    )


@router.post("", response_model=StoryOut, status_code=status.HTTP_201_CREATED)
def create_story(
    caption: str | None = Form(default=None, max_length=2000),
    files: list[UploadFile] | None = File(default=None),
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: publish a 24-hour story.

    Requires at least one image; returns the created story with its media
    (each with an auth-gated ``/stories/{story_id}/media?media_id={id}`` url).
    The story expires 24 hours from now and is visible to the creator and
    their active followers only. All files are validated before any row or
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

    story = Story(
        creator_id=user.id,
        caption=caption,
        expires_at=StoryService._now() + STORY_TTL,
    )
    db.add(story)
    db.flush()  # assign story.id without committing yet

    # If the DB commit fails, clean up the originals we already wrote so a
    # failed upload never leaves orphaned bytes behind.
    written_keys: list[str] = []
    try:
        for original, media_type, ext in prepared:
            storage_key = f"{uuid.uuid4().hex}{ext}"
            # The unwatermarked original goes to the private store — it is
            # never served to clients. Serving watermarks it on the fly per
            # viewer, exactly like post media.
            save_original(original, storage_key)
            written_keys.append(storage_key)
            db.add(
                StoryMedia(
                    story_id=story.id,
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

    db.refresh(story)
    return _story_out(story)


@router.get("/{creator_id}", response_model=list[StoryOut])
def list_creator_stories(
    creator_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """A creator's active 24-hour stories (follower-only).

    Anonymous and registered-but-unsubscribed viewers get ``403`` — story
    content is exclusive to active followers (and the creator themselves).
    Expired stories are never returned here.
    """
    creator = db.get(User, creator_id)
    if creator is None or creator.role != UserRole.creator or not creator.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Creator not found",
        )

    ctx = resolve_viewer_context(request, creator_id, db)
    is_owner = ctx.user is not None and ctx.user.id == creator_id
    if ctx.is_anonymous:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not ctx.is_follower and not is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Follower subscription required to view stories",
        )
    stories = StoryService(db).active_stories(creator_id)
    return [_story_out(story) for story in stories]


@router.api_route("/{story_id}/media", methods=["GET", "HEAD"])
def serve_story_media(
    story_id: int,
    request: Request,
    media_id: int = Query(..., description="Id of the media file within the story"),
    db: Session = Depends(get_db),
):
    """Serve one of a story's media files to an authorized viewer, watermarked.

    Authenticates (Bearer header or ``?token=`` query for ``<img>`` tags),
    authorizes (the story's creator or an active follower), enforces the 24h
    expiry, then serves the per-viewer watermarked bytes — the original is
    never exposed. Expired stories return ``404`` (they no longer exist).
    """
    story = db.get(Story, story_id)
    if story is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Story not found",
        )
    # Expiry is a hard gate: an expired story is gone, exactly like a deleted
    # post (404 — the media url that worked an hour ago must stop working).
    # ``_as_utc`` normalizes SQLite's naive reads before comparing to now.
    if StoryService._as_utc(story.expires_at) <= StoryService._now():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Story has expired",
        )

    ctx = resolve_viewer_context(request, story.creator_id, db)
    is_owner = ctx.user is not None and ctx.user.id == story.creator_id
    if ctx.is_anonymous:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not ctx.is_follower and not is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Follower subscription required",
        )

    media = db.get(StoryMedia, media_id)
    if media is None or media.story_id != story_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media not found",
        )

    storage = get_original_storage()
    if not storage.exists(media.storage_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media file missing",
        )

    storage_key = media.storage_key
    media_type = media.media_type

    # Stories carry no per-viewer engagement counters; the watermark still
    # traces a leaked capture back to the viewer (``post_id=None`` -> the
    # legacy 3-field form, since the capture is of a story, not a post).
    user_ref = f"user:{ctx.user.id}"
    watermarked, cache_status = serve_media(storage_key, user_ref, post_id=None)
    return Response(
        content=watermarked,
        media_type=served_content_type(media_type),
        headers={
            "Cache-Control": "no-store",
            "X-Watermark": user_ref,
            "X-Watermark-Cache": cache_status,
            "X-Content-Type-Options": "nosniff",
        },
    )


@dashboard_router.get("", response_model=list[StoryOut])
def list_creator_own_stories(
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: every own story, expired included, newest first.

    The dashboard needs the full history (including what already auto-expired)
    so the UI can render a "last story" state — unlike the follower listing,
    which only ever shows live stories.
    """
    stories = StoryService(db).all_stories(user.id)
    return [_story_out(story) for story in stories]


@dashboard_router.delete("/{story_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_story(
    story_id: int,
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: delete one of their own stories.

    Media rows cascade (the FK is ``ON DELETE CASCADE``), and the private
    originals are removed from storage so an expired-then-deleted story leaves
    no bytes behind.
    """
    story = db.get(Story, story_id)
    if story is None or story.creator_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Story not found",
        )
    for media in story.media:
        delete_original(media.storage_key)
    try:
        db.delete(story)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return Response(status_code=status.HTTP_204_NO_CONTENT)
