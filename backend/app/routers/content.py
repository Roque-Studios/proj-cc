"""Secure content-media serving and paid-broadcast unlocks.

``GET/HEAD /content/{post_id}/media?media_id={id}`` streams one of a post's
media files to a viewer who is allowed to see it — the post's creator, or an
active follower (paid subscription / trial with a current period) who has
unlocked any paid broadcast. The response is always the private original
**watermarked on the fly** with the requesting viewer's identity
(``app.watermark``); the unwatermarked original is never served, and no public
URL can reach the private store (``app.storage``).

``POST /content/{post_id}/unlock`` is the one-time paid unlock for paid
broadcasts (see ``app.services.broadcasts``).

Authorization order for media:

- ``404`` — unknown post;
- ``401`` — no valid access token (``Authorization: Bearer`` or ``?token=`` for
  ``<img>`` tags);
- ``403`` — authenticated but not a follower of the post's creator;
- ``403`` — follower whose paid broadcast is still locked (no one-time unlock);
- ``404`` — media id that doesn't belong to the post.

Media responses always carry ``Cache-Control: no-store`` (the watermark is
volatile) plus the ``X-Watermark`` viewer ref and ``X-Watermark-Cache``
hit/miss headers.
"""

from __future__ import annotations

import structlog
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from ..access import resolve_viewer_context
from ..database import get_db
from ..media import serve_media, served_content_type
from ..models import Post, PostMedia
from ..schemas import UnlockResponse
from ..services.broadcasts import BroadcastService, PaymentFailedError
from ..storage import get_original_storage

logger = structlog.get_logger()

router = APIRouter(prefix="/content", tags=["content"])


@router.api_route("/{post_id}/media", methods=["GET", "HEAD"])
def serve_post_media(
    post_id: int,
    request: Request,
    media_id: int = Query(..., description="Id of the media file within the post"),
    db: Session = Depends(get_db),
):
    """Serve one of a post's media files to an authorized viewer, watermarked.

    Authenticates (Bearer header or ``?token=`` query for ``<img>`` tags),
    authorizes (the post's creator or an active follower with the broadcast
    unlocked), then serves the per-viewer watermarked bytes from the Redis
    cache or a fresh render. The original unwatermarked file is never exposed:
    only internal service code reads the private store, and every response is
    a re-encoded transform.
    """
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )

    ctx = resolve_viewer_context(request, post.creator_id, db)
    if ctx.is_anonymous:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # The post's creator always has access to their own content; everyone else
    # needs an active (or trialing) subscription with a current period.
    is_owner = ctx.user is not None and ctx.user.id == post.creator_id
    if not ctx.is_follower and not is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Follower subscription required",
        )
    # Paid broadcasts stay locked until the one-time unlock is paid. The owner
    # always has full access; a follower sees only the locked preview
    # (metadata from the feed) until they unlock.
    if not is_owner and post.broadcast_price_cents is not None:
        if not BroadcastService(db).is_unlocked(ctx.user.id, post.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Broadcast is locked — one-time unlock required",
            )

    media = db.get(PostMedia, media_id)
    if media is None or media.post_id != post_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media not found",
        )

    storage = get_original_storage()
    if not storage.exists(media.storage_key):
        logger.warning(
            "Media row exists but original file is missing",
            post_id=post_id,
            media_id=media_id,
            storage_key=media.storage_key,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media file missing",
        )

    # The viewer identity is known only after authz, so the watermark (and its
    # per-viewer cache entry) is resolved here — never before. ``post_id`` is
    # embedded in the watermark text so a leaked capture traces back to the
    # post as well as the viewer (see app.watermark_trace).
    user_ref = f"user:{ctx.user.id}"
    watermarked, cache_status = serve_media(media.storage_key, user_ref, post_id=post.id)
    return Response(
        content=watermarked,
        media_type=served_content_type(media.media_type),
        headers={
            "Cache-Control": "no-store",
            "X-Watermark": user_ref,
            "X-Watermark-Cache": cache_status,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{post_id}/unlock", response_model=UnlockResponse)
def unlock_broadcast(
    post_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Pay the one-time price and unlock a paid broadcast's full content.

    Subscriber-only (an active subscription to the broadcast's creator is
    required). Idempotent: repeating an already-paid unlock returns the
    existing ``BroadcastUnlock`` row without a second charge.
    """
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )

    ctx = resolve_viewer_context(request, post.creator_id, db)
    if ctx.is_anonymous:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if post.broadcast_price_cents is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This post is not a paid broadcast",
        )
    is_owner = ctx.user is not None and ctx.user.id == post.creator_id
    if is_owner:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are the creator of this broadcast — you already have full access",
        )
    if not ctx.is_follower:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Follower subscription required to unlock broadcasts",
        )

    service = BroadcastService(db)
    existing = service.get_unlock(ctx.user.id, post.id)
    if existing is not None:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=UnlockResponse(
                post_id=post.id,
                broadcast_price_cents=post.broadcast_price_cents,
                already_unlocked=True,
                unlock=existing,
            ).model_dump(mode="json"),
        )

    try:
        unlock, _created = service.unlock(ctx.user.id, post)
    except PaymentFailedError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=str(exc),
        )
    # ``broadcast_price_cents`` was pre-checked above, so the service's
    # BroadcastNotPaidError can't fire here.
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=UnlockResponse(
            post_id=post.id,
            broadcast_price_cents=post.broadcast_price_cents,
            already_unlocked=False,
            unlock=unlock,
        ).model_dump(mode="json"),
    )
