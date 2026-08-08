"""Creator block/ban management.

A creator can **block (ban)** a user who misbehaves (harassment, abuse, …).
Blocking is a hard, immediate access revocation for that (creator, user)
pair:

- the blocked user is demoted from ``follower`` in the access resolver — every
  content gate (feed, media, stories, engagement, unlocks) treats them as a
  registered viewer;
- DMs to the creator are rejected (the messaging gate checks the block);
- ``POST /subscribe`` rejects the blocked user with a clear 403;
- any **active subscription** they hold with the creator is canceled locally
  (the row flips to ``canceled`` — no gateway call, so no charge is reversed:
  the subscriber keeps what they paid for but loses access until unblocked).

Endpoints (all creator-only via ``require_creator``):

- ``GET /creator/blocked`` — paginated list of blocked users (newest first);
- ``POST /creator/blocked`` — block a user (idempotent; 400 self-block, 404
  unknown user);
- ``DELETE /creator/blocked/{user_id}`` — unblock (idempotent; the user can
  re-subscribe and access returns on their next checkout).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import require_creator
from ..models import BlockedUser, Subscription, SubscriptionStatus, User, UserRole
from ..schemas import BlockedUserListOut, BlockedUserOut, BlockUserRequest

router = APIRouter(prefix="/creator/blocked", tags=["creator"])


def _blocked_out(
    blocked: BlockedUser,
    user: User,
    subscription: Subscription | None,
    *,
    prior_status: str | None = None,
) -> BlockedUserOut:
    """Shape a blocked-user row with the blocked user's identity + sub context.

    ``prior_status`` reports the subscription status **before** the block
    canceled it (the list shows a subscriber who was blocked as "was active"
    rather than the post-block canceled state).
    """
    was_subscriber = subscription is not None
    status = None
    if was_subscriber:
        # Report the status the user had when they were blocked (a canceled
        # row shows "was active", not the post-block canceled state).
        status = (
            prior_status
            if prior_status is not None
            else subscription.status.value
        )
    return BlockedUserOut(
        id=blocked.id,
        user_id=user.id,
        username=user.username,
        email=user.email,
        blocked_at=blocked.created_at,
        was_subscriber=was_subscriber,
        subscription_status=status,
    )


@router.get("", response_model=BlockedUserListOut)
def list_blocked(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: paginated list of blocked users (newest first)."""
    total = (
        db.scalar(
            select(func.count())
            .select_from(BlockedUser)
            .where(BlockedUser.creator_id == user.id)
        )
        or 0
    )
    rows = db.scalars(
        select(BlockedUser)
        .where(BlockedUser.creator_id == user.id)
        .order_by(BlockedUser.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items: list[BlockedUserOut] = []
    for blocked in rows:
        target = db.get(User, blocked.user_id)
        if target is None:
            continue  # user deleted — the FK cascade removes the row anyway
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.subscriber_id == blocked.user_id,
                Subscription.creator_id == user.id,
            )
        )
        items.append(_blocked_out(blocked, target, subscription))
    return BlockedUserListOut(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


@router.post(
    "",
    response_model=BlockedUserOut,
    status_code=status.HTTP_201_CREATED,
)
def block_user(
    payload: BlockUserRequest,
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: block (ban) a user. Idempotent — re-blocking a blocked
    user returns the existing row (200-shaped response via the 201 only the
    first time; a repeat returns the same row)."""
    if payload.user_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot block yourself",
        )
    target = db.get(User, payload.user_id)
    if target is None or not target.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    if target.role == UserRole.creator:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You can only block subscribers",
        )

    blocked = db.scalar(
        select(BlockedUser).where(
            BlockedUser.creator_id == user.id,
            BlockedUser.user_id == target.id,
        )
    )
    if blocked is None:
        blocked = BlockedUser(creator_id=user.id, user_id=target.id)
        db.add(blocked)
        try:
            db.flush()
        except IntegrityError:
            # A concurrent block won the race — reuse the existing row.
            db.rollback()
            blocked = db.scalar(
                select(BlockedUser).where(
                    BlockedUser.creator_id == user.id,
                    BlockedUser.user_id == target.id,
                )
            )
            if blocked is None:  # pragma: no cover - defensive
                raise

    # Cancel any active subscription immediately: access stops now (the
    # access resolver demotes blocked users to ``registered``), and the row
    # reads as ``canceled`` in the dashboard so the block is unambiguous.
    # Local-only: no gateway cancel call, so no charge is reversed — the
    # subscriber keeps their paid period, they just can't use it while blocked.
    subscription = db.scalar(
        select(Subscription).where(
            Subscription.subscriber_id == target.id,
            Subscription.creator_id == user.id,
        )
    )
    was_subscriber = False
    prior_status: str | None = None
    if subscription is not None and subscription.status in (
        SubscriptionStatus.active,
        SubscriptionStatus.trialing,
        SubscriptionStatus.past_due,
        SubscriptionStatus.incomplete,
    ):
        prior_status = subscription.status.value
        subscription.status = SubscriptionStatus.canceled
        subscription.checkout_url = None
        subscription.cancel_at_period_end = False
        # Detach the gateway ref so a late webhook for an in-flight payment
        # (started before the block) can never find and reactivate this row —
        # the block stands even if the money later lands.
        subscription.external_ref = None
        was_subscriber = True
    db.commit()
    db.refresh(blocked)
    return _blocked_out(
        blocked,
        target,
        subscription if was_subscriber else None,
        prior_status=prior_status,
    )


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def unblock_user(
    user_id: int,
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: unblock a user (idempotent — unknown blocks are a no-op)."""
    blocked = db.scalar(
        select(BlockedUser).where(
            BlockedUser.creator_id == user.id,
            BlockedUser.user_id == user_id,
        )
    )
    if blocked is not None:
        db.delete(blocked)
        db.commit()
    return None
