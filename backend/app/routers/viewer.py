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
from ..gateways import GATEWAYS
from ..models import Post, PostComment, PostLike, User, UserRole
from ..schemas import CheckoutGatewayOut, FeedResponse, build_post_out
from ..services.broadcasts import BroadcastService
from ..services.gateways import enabled_configured_gateways

router = APIRouter(prefix="/creators", tags=["viewer"])


def _engagement_totals(
    db: Session, post_ids: list[int], viewer_id: int | None
) -> tuple[dict[int, int], dict[int, int], set[int]]:
    """Bulk like/comment counts + the viewer's liked post ids for a page.

    ``liked_by_me`` is only computed when ``viewer_id`` is provided (an
    authenticated viewer); anonymous requests get an empty set.
    """
    if not post_ids:
        return {}, {}, set()
    like_counts = dict(
        db.execute(
            select(PostLike.post_id, func.count(PostLike.id))
            .where(PostLike.post_id.in_(post_ids))
            .group_by(PostLike.post_id)
        ).all()
    )
    comment_counts = dict(
        db.execute(
            select(PostComment.post_id, func.count(PostComment.id))
            .where(PostComment.post_id.in_(post_ids))
            .group_by(PostComment.post_id)
        ).all()
    )
    liked_ids: set[int] = set()
    if viewer_id is not None:
        liked_ids = set(
            db.scalars(
                select(PostLike.post_id).where(
                    PostLike.post_id.in_(post_ids),
                    PostLike.user_id == viewer_id,
                )
            ).all()
        )
    return like_counts, comment_counts, liked_ids


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
    and media counts, with media urls withheld (``teaser: true``). Posts the
    creator hid (``is_visible=False``) are excluded from the feed entirely —
    the creator manages them from the content dashboard.
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
            .where(
                Post.creator_id == creator_id,
                Post.is_visible.is_(True),
            )
        )
        or 0
    )
    posts = db.scalars(
        select(Post)
        .options(selectinload(Post.media))
        .where(
            Post.creator_id == creator_id,
            Post.is_visible.is_(True),
        )
        .order_by(Post.created_at.desc(), Post.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    like_counts, comment_counts, liked_ids = _engagement_totals(
        db,
        [p.id for p in posts],
        ctx.user.id if ctx.user is not None else None,
    )

    # Followers see the full feed; the creator viewing their **own** feed is
    # treated the same — their paid broadcasts are never locked for them (the
    # owner always has full access, matching the media + unlock endpoints).
    is_owner = ctx.user is not None and ctx.user.id == creator_id
    if ctx.is_follower or is_owner:
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
                and not is_owner
            )
            items.append(
                build_post_out(
                    post,
                    unlocked=False if is_locked else (
                        True if post.broadcast_price_cents is not None else None
                    ),
                    include_media_urls=not is_locked,
                    # Locked broadcasts show a blurred preview until unlocked.
                    include_preview_urls=is_locked,
                    like_count=like_counts.get(post.id, 0),
                    comment_count=comment_counts.get(post.id, 0),
                    liked_by_me=post.id in liked_ids,
                )
            )
        teaser = False
    else:
        # Non-follower teaser: metadata only, real urls withheld — each media
        # carries a blurred public preview url instead. Engagement counts are
        # public; the actions themselves stay gated (like the media).
        items = [
            build_post_out(
                post,
                unlocked=False if post.broadcast_price_cents is not None else None,
                include_media_urls=False,
                include_preview_urls=True,
                like_count=like_counts.get(post.id, 0),
                comment_count=comment_counts.get(post.id, 0),
                liked_by_me=post.id in liked_ids,
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


@router.get("/{creator_id}/gateways", response_model=list[CheckoutGatewayOut])
def creator_checkout_gateways(
    creator_id: int,
    db: Session = Depends(get_db),
):
    """Gateways a subscriber can pay with for this creator (public).

    Returns **only** the creator's enabled gateways with a complete config —
    disabled, incomplete, or unknown gateways never appear, so subscriber
    checkout only ever shows what the creator actually accepts.
    """
    creator = db.get(User, creator_id)
    if creator is None or creator.role != UserRole.creator or not creator.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Creator not found",
        )
    return [
        CheckoutGatewayOut(gateway=gateway, label=GATEWAYS[gateway].label)
        for gateway, _row in enabled_configured_gateways(db, creator_id)
    ]
