"""Post engagement: likes and comments.

``POST /posts/{post_id}/like`` toggles a like on (idempotent — liking twice
keeps one row), ``DELETE /posts/{post_id}/like`` removes it; both return the
new ``like_count`` so the client never refetches. Comments are text-only
(validated 1..500 chars, blank rejected) with a paginated list
(``GET /posts/{post_id}/comments``, newest first), creation
(``POST /posts/{post_id}/comments``) and deletion
(``DELETE /posts/{post_id}/comments/{comment_id}`` — by the comment's author
or the post's creator).

Like posts themselves, engagement follows the **content gate**: only the
post's creator and their active followers may like/read/write comments (the
feed reports the counts to everyone, but the actions are gated). Unknown or
hidden posts ``404`` so post ids can't be probed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..access import is_active_follower
from ..database import get_db
from ..deps import get_current_user
from ..models import Post, PostComment, PostLike, User, UserRole
from ..schemas import (
    CommentCreate,
    CommentOut,
    CommentsPageOut,
    PostLikeResponse,
)

router = APIRouter(prefix="/posts", tags=["posts engagement"])


def _gated_post(db: Session, post_id: int, user: User) -> Post:
    """The post, gated to its creator + active followers (404/403 otherwise)."""
    post = db.get(Post, post_id)
    if post is None or not post.is_visible:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )
    # The canonical follower definition (access.is_active_follower) — the
    # post's creator always counts, and ``user`` was already resolved by
    # ``get_current_user``, so no token re-resolution happens here.
    if user.id != post.creator_id and not is_active_follower(
        db, user.id, post.creator_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only followers can interact with this post",
        )
    return post


def _comment_out(comment: PostComment, author: User) -> CommentOut:
    """Shape one comment for the API, with the author's display context."""
    profile = author.creator_profile
    return CommentOut(
        id=comment.id,
        post_id=comment.post_id,
        user_id=comment.user_id,
        body=comment.body,
        author_username=author.username,
        author_display_name=profile.display_name if profile else None,
        author_avatar_url=profile.avatar_url if profile else None,
        author_is_creator=author.role == UserRole.creator,
        created_at=comment.created_at,
    )


@router.post("/{post_id}/like", response_model=PostLikeResponse)
def like_post(
    post_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Like a post (idempotent — a second like keeps the single row)."""
    _gated_post(db, post_id, user)
    existing = db.scalar(
        select(PostLike).where(
            PostLike.post_id == post_id,
            PostLike.user_id == user.id,
        )
    )
    if existing is None:
        db.add(PostLike(post_id=post_id, user_id=user.id))
        try:
            db.commit()
        except IntegrityError:
            # Concurrent duplicate like from the same user (two tabs / a
            # network retry): the unique pair already exists — treat it as
            # the idempotent success it was meant to be.
            db.rollback()
    like_count = (
        db.scalar(
            select(func.count())
            .select_from(PostLike)
            .where(PostLike.post_id == post_id)
        )
        or 0
    )
    return PostLikeResponse(post_id=post_id, liked=True, like_count=like_count)


@router.delete("/{post_id}/like", response_model=PostLikeResponse)
def unlike_post(
    post_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a like from a post (idempotent — unliking an unliked post is a no-op)."""
    _gated_post(db, post_id, user)
    db.execute(
        delete(PostLike).where(
            PostLike.post_id == post_id,
            PostLike.user_id == user.id,
        )
    )
    db.commit()
    like_count = (
        db.scalar(
            select(func.count())
            .select_from(PostLike)
            .where(PostLike.post_id == post_id)
        )
        or 0
    )
    return PostLikeResponse(post_id=post_id, liked=False, like_count=like_count)


@router.get("/{post_id}/comments", response_model=CommentsPageOut)
def list_comments(
    post_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Paginated comments of a post (newest first) — followers/owner only."""
    _gated_post(db, post_id, user)
    total = (
        db.scalar(
            select(func.count())
            .select_from(PostComment)
            .where(PostComment.post_id == post_id)
        )
        or 0
    )
    comments = db.scalars(
        select(PostComment)
        .where(PostComment.post_id == post_id)
        .order_by(PostComment.created_at.desc(), PostComment.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    authors = {
        u.id: u
        for u in db.scalars(
            select(User)
            .options(selectinload(User.creator_profile))
            .where(User.id.in_({c.user_id for c in comments}))
        ).all()
    } if comments else {}
    return CommentsPageOut(
        items=[_comment_out(c, authors[c.user_id]) for c in comments],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


@router.post(
    "/{post_id}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_comment(
    post_id: int,
    payload: CommentCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Comment on a post (text + emojis, 1..500 chars) — followers/owner only."""
    _gated_post(db, post_id, user)
    comment = PostComment(post_id=post_id, user_id=user.id, body=payload.body)
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return _comment_out(comment, user)


@router.delete(
    "/{post_id}/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_comment(
    post_id: int,
    comment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a comment — by its author, or by the post's creator."""
    post = _gated_post(db, post_id, user)
    comment = db.get(PostComment, comment_id)
    if comment is None or comment.post_id != post_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found",
        )
    if comment.user_id != user.id and user.id != post.creator_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own comments",
        )
    db.delete(comment)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
