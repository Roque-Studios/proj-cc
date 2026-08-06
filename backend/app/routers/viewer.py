"""Viewer endpoints.

``GET /creators/{creator_id}/access`` exposes the resolved access level
(anonymous / registered / follower) for a creator; ``GET
/creators/{creator_id}/posts`` is the follower-gated, paginated feed. Route
handlers depend on ``access.resolve_viewer_access`` to gate content per viewer
level.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..access import ViewerContext, resolve_viewer_access
from ..database import get_db
from ..models import Post, User, UserRole
from ..schemas import FeedResponse, build_post_out
from ..services.broadcasts import BroadcastService

router = APIRouter(prefix="/creators", tags=["viewer"])


@router.get("/{creator_id}/access")
def viewer_access(
    ctx: ViewerContext = Depends(resolve_viewer_access()),
) -> dict:
    """Return the current viewer's access level for this creator."""
    return {
        "level": ctx.level.value,
        "user_id": ctx.user.id if ctx.user else None,
        "subscription": ctx.subscription.status.value if ctx.subscription else None,
        "creator": {
            "id": ctx.creator.id,
            "username": ctx.creator.username,
        }
        if ctx.creator is not None
        else None,
    }


@router.get("/{creator_id}/posts", response_model=FeedResponse)
def creator_feed(
    creator_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    ctx: ViewerContext = Depends(resolve_viewer_access()),
    db: Session = Depends(get_db),
):
    """Follower-gated, paginated feed of a creator's posts (newest first).

    Active followers (or trialing) get the full posts, media urls included —
    except **paid broadcasts** they haven't unlocked, which come back as a
    locked preview (``unlocked: false``, media urls withheld, one-time price
    shown). Anonymous and registered non-followers get a teaser: post captions
    and media counts, with media urls withheld (``teaser: true``).
    """
    creator = db.get(User, creator_id)
    if creator is None or creator.role != UserRole.creator or not creator.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Creator not found",
        )

    total = (
        db.scalar(
            select(func.count())
            .select_from(Post)
            .where(Post.creator_id == creator_id)
        )
        or 0
    )
    posts = db.scalars(
        select(Post)
        .options(selectinload(Post.media))
        .where(Post.creator_id == creator_id)
        .order_by(Post.created_at.desc(), Post.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    if ctx.is_follower:
        unlocked_ids = (
            BroadcastService(db).unlocked_post_ids(ctx.user.id, [p.id for p in posts])
            if ctx.user is not None
            else set()
        )
        items = []
        for post in posts:
            is_locked = (
                post.broadcast_price_cents is not None
                and post.id not in unlocked_ids
            )
            items.append(
                build_post_out(
                    post,
                    unlocked=False if is_locked else (
                        True if post.broadcast_price_cents is not None else None
                    ),
                    include_media_urls=not is_locked,
                )
            )
        teaser = False
    else:
        # Non-follower teaser: metadata only, urls withheld. A paid broadcast
        # shows its price but stays locked (unlocked=False).
        items = [
            build_post_out(
                post,
                unlocked=False if post.broadcast_price_cents is not None else None,
                include_media_urls=False,
            )
            for post in posts
        ]
        teaser = True

    return FeedResponse(
        teaser=teaser,
        posts=items,
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )
