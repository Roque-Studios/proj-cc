"""Creator content dashboard: manage posts/broadcasts.

``GET /creator/content`` lists the creator's own posts/broadcasts, newest
first, with the engagement stats the dashboard shows — ``view_count`` (media
views served to non-owners) and ``unlock_count`` (active one-time unlocks of a
paid broadcast; refunded unlocks are excluded). ``PATCH /creator/content/{id}``
updates the caption and/or the visibility toggle (``is_visible=False``
soft-archives the post: the public feed excludes it and non-owner media/unlock
requests 404, but the creator keeps full access through this dashboard).
``DELETE /creator/content/{id}`` removes the post, its media rows and the
private originals from storage.

Every route is **creator-only** and scoped to the caller: another creator's
post is ``404`` (never ``403``), so post ids can't be probed across creators.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import require_creator
from ..media import delete_original
from ..models import PaidUnlock, Post, PostComment, PostLike, User
from ..schemas import CreatorPostOut, PostMediaOut, PostUpdate

router = APIRouter(prefix="/creator/content", tags=["creator"])


def _engagement_counts(db: Session, post_ids: list[int]) -> tuple[dict[int, int], dict[int, int]]:
    """Bulk like/comment totals per post for the dashboard cards."""
    if not post_ids:
        return {}, {}
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
    return like_counts, comment_counts


def _get_own_post(db: Session, creator_id: int, post_id: int) -> Post:
    """The creator's own post, or 404 (indistinguishable from missing)."""
    post = db.get(Post, post_id)
    if post is None or post.creator_id != creator_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )
    return post


def _creator_post_out(
    post: Post,
    unlock_counts: dict[int, int],
    like_counts: dict[int, int] | None = None,
    comment_counts: dict[int, int] | None = None,
) -> CreatorPostOut:
    """Dashboard shape for one post — media urls always included (owner view)."""
    like_counts = like_counts or {}
    comment_counts = comment_counts or {}
    return CreatorPostOut(
        id=post.id,
        caption=post.caption,
        broadcast_price_cents=post.broadcast_price_cents,
        is_visible=post.is_visible,
        created_at=post.created_at,
        updated_at=post.updated_at,
        media_count=len(post.media),
        view_count=post.view_count,
        unlock_count=unlock_counts.get(post.id, 0),
        like_count=like_counts.get(post.id, 0),
        comment_count=comment_counts.get(post.id, 0),
        media=[
            PostMediaOut(
                id=media.id,
                media_type=media.media_type,
                media_url=media.media_url,
                created_at=media.created_at,
            )
            for media in post.media
        ],
    )


def _active_unlock_counts(db: Session, post_ids: list[int]) -> dict[int, int]:
    """Unlocks still in force per post (refunded ones are excluded)."""
    if not post_ids:
        return {}
    rows = db.execute(
        select(PaidUnlock.post_id, func.count(PaidUnlock.id))
        .where(
            PaidUnlock.post_id.in_(post_ids),
            PaidUnlock.refunded_at.is_(None),
        )
        .group_by(PaidUnlock.post_id)
    ).all()
    return dict(rows)


@router.get("", response_model=list[CreatorPostOut])
def list_creator_content(
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: every own post/broadcast with engagement stats, newest first."""
    posts = db.scalars(
        select(Post)
        .options(selectinload(Post.media))
        .where(Post.creator_id == user.id)
        .order_by(Post.created_at.desc(), Post.id.desc())
    ).all()
    unlock_counts = _active_unlock_counts(db, [post.id for post in posts])
    like_counts, comment_counts = _engagement_counts(db, [post.id for post in posts])
    return [
        _creator_post_out(post, unlock_counts, like_counts, comment_counts)
        for post in posts
    ]


@router.patch("/{post_id}", response_model=CreatorPostOut)
def update_creator_post(
    post_id: int,
    payload: PostUpdate,
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: edit a post's caption and/or visibility (own posts only)."""
    post = _get_own_post(db, user.id, post_id)
    updates = payload.model_dump(exclude_unset=True)
    if "caption" in updates:
        # null or whitespace-only clears the caption.
        raw = updates["caption"]
        post.caption = (raw or "").strip() or None
    if "is_visible" in updates:
        post.is_visible = updates["is_visible"]
    db.commit()
    db.refresh(post)
    like_counts, comment_counts = _engagement_counts(db, [post.id])
    return _creator_post_out(
        post,
        _active_unlock_counts(db, [post.id]),
        like_counts,
        comment_counts,
    )


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_creator_post(
    post_id: int,
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: delete a post — media rows cascade, unlock rows are
    deleted explicitly (the FK cascade is a Postgres property; explicit
    deletes keep this correct on any backend), and the private originals are
    removed from storage."""
    post = _get_own_post(db, user.id, post_id)
    for media in post.media:
        delete_original(media.storage_key)
    # Originals are already removed from storage; if the DB commit fails the
    # rows roll back (media then 404s gracefully on the missing file) rather
    # than leaving a half-deleted post.
    try:
        db.execute(delete(PaidUnlock).where(PaidUnlock.post_id == post_id))
        # Likes/comments cascade via the DB FK (Postgres) and the ORM
        # relationships; the explicit deletes keep this correct on any backend
        # (SQLite tests don't enforce FKs).
        db.execute(delete(PostLike).where(PostLike.post_id == post_id))
        db.execute(delete(PostComment).where(PostComment.post_id == post_id))
        db.delete(post)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return Response(status_code=status.HTTP_204_NO_CONTENT)
