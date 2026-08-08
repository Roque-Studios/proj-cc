"""Creator subscriber management: who subscribes and what they've paid.

``GET /creator/subscribers`` lists the **owning creator's** subscriptions
(paginated, optionally filtered by status) with each subscriber's identity and
start date, plus a global revenue summary. The revenue totals are the sums of
**completed** payments in the ``payment`` ledger — monthly subscription
payments plus one-time broadcast unlocks, refunds excluded — so they always
match the sum of completed payments in the DB by construction.

Access is strictly the owning creator: ``require_creator`` (401 anonymous, 403
registered users), and the query is scoped to ``user.id`` — a creator can only
ever see their own subscribers and revenue.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import require_creator
from ..models import BlockedUser, Payment, Subscription, SubscriptionStatus, User
from ..schemas import RevenueSummaryOut, SubscriberListOut, SubscriberOut

router = APIRouter(prefix="/creator/subscribers", tags=["creator"])

_VALID_STATUSES = {s.value for s in SubscriptionStatus}


def _revenue_summary(db: Session, creator_id: int) -> RevenueSummaryOut:
    """Sums of completed payments + per-status subscriber counts (global)."""
    ledger_rows = db.execute(
        select(Payment.kind, Payment.status, func.coalesce(func.sum(Payment.amount_cents), 0))
        .where(Payment.creator_id == creator_id)
        .group_by(Payment.kind, Payment.status)
    ).all()
    monthly = sum(
        amount
        for kind, payment_status, amount in ledger_rows
        if kind == "subscription" and payment_status == "completed"
    )
    one_time = sum(
        amount
        for kind, payment_status, amount in ledger_rows
        if kind == "unlock" and payment_status == "completed"
    )
    count_rows = db.execute(
        select(Subscription.status, func.count(Subscription.id))
        .where(Subscription.creator_id == creator_id)
        .group_by(Subscription.status)
    ).all()
    counts = {row[0]: row[1] for row in count_rows}
    active = counts.get(SubscriptionStatus.active, 0)
    trialing = counts.get(SubscriptionStatus.trialing, 0)
    past_due = counts.get(SubscriptionStatus.past_due, 0)
    canceled = counts.get(SubscriptionStatus.canceled, 0)
    return RevenueSummaryOut(
        monthly_revenue_cents=monthly,
        one_time_revenue_cents=one_time,
        total_revenue_cents=monthly + one_time,
        active_subscribers=active,
        trialing_subscribers=trialing,
        past_due_subscribers=past_due,
        canceled_subscribers=canceled,
        total_subscribers=active + trialing + past_due + canceled
        + counts.get(SubscriptionStatus.incomplete, 0)
        + counts.get(SubscriptionStatus.expired, 0),
    )


def _subscriber_out(subscription: Subscription) -> SubscriberOut:
    return SubscriberOut(
        subscription_id=subscription.id,
        subscriber_id=subscription.subscriber_id,
        subscriber_email=subscription.subscriber.email,
        subscriber_username=subscription.subscriber.username,
        status=subscription.status.value,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        cancel_at_period_end=subscription.cancel_at_period_end,
        started_at=subscription.created_at,
        payment_provider=subscription.payment_provider,
    )


@router.get("", response_model=SubscriberListOut)
def list_subscribers(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    status_filter: str | None = Query(default=None, alias="status"),
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: paginated subscriber list, optionally filtered by status.

    ``status`` is one of the subscription statuses (active, trialing,
    incomplete, past_due, canceled, expired). The revenue summary is global to
    the creator (not filtered) — it always equals the sum of completed
    payments in the ledger. Blocked (banned) users are excluded entirely —
    they live on the Blocked tab, not in the subscriber list.
    """
    if status_filter is not None and status_filter not in _VALID_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown subscription status: {status_filter}",
        )

    filters = [Subscription.creator_id == user.id]
    if status_filter is not None:
        filters.append(Subscription.status == SubscriptionStatus(status_filter))
    # Blocked (banned) users are managed on the Blocked tab — they never
    # appear here, not even as canceled rows.
    blocked_ids = db.scalars(
        select(BlockedUser.user_id).where(BlockedUser.creator_id == user.id)
    ).all()
    if blocked_ids:
        filters.append(Subscription.subscriber_id.notin_(blocked_ids))

    total = (
        db.scalar(
            select(func.count()).select_from(Subscription).where(*filters)
        )
        or 0
    )
    subscriptions = db.scalars(
        select(Subscription)
        .options(selectinload(Subscription.subscriber))
        .where(*filters)
        .order_by(Subscription.created_at.desc(), Subscription.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return SubscriberListOut(
        items=[_subscriber_out(s) for s in subscriptions],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
        summary=_revenue_summary(db, user.id),
    )
