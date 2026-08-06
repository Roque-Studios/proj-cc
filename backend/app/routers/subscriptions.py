"""Subscription endpoints.

- ``POST /subscribe`` starts a subscription to a creator at the single defined
  monthly tier (``SUBSCRIPTION_TIER_PLAN_ID``), opening a hosted checkout with
  the configured gateway. The row is created as ``incomplete`` (pending
  payment); a successful payment webhook activates it, a failed one leaves it
  incomplete.
- ``POST /cancel`` sets non-renew on a subscription: access persists until
  ``current_period_end``, then a scheduled task expires it to ``canceled``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import Subscription, SubscriptionStatus, User, UserRole
from ..schemas import CancelRequest, SubscribeRequest, SubscribeResponse, SubscriptionOut
from ..services.subscriptions import SubscriptionService

router = APIRouter(tags=["subscriptions"])


@router.post(
    "/subscribe",
    response_model=SubscribeResponse,
    status_code=status.HTTP_201_CREATED,
)
def subscribe(
    payload: SubscribeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start a subscription to a creator at the single monthly tier.

    Returns the pending (``incomplete``) subscription and the hosted checkout
    url the client should redirect the subscriber to for payment.
    """
    creator = db.get(User, payload.creator_id)
    if creator is None or creator.role != UserRole.creator or not creator.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Creator not found",
        )
    if creator.id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot subscribe to yourself",
        )

    service = SubscriptionService(db)
    subscription = service.create_subscription(
        subscriber_id=user.id,
        creator_id=creator.id,
        plan_id=settings.SUBSCRIPTION_TIER_PLAN_ID,
        success_url=payload.success_url,
        cancel_url=payload.cancel_url,
    )
    return SubscribeResponse(
        subscription=SubscriptionOut.model_validate(subscription),
        checkout_url=subscription.checkout_url,
        status=subscription.status.value,
    )


@router.post("/cancel", response_model=SubscriptionOut)
def cancel(
    payload: CancelRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Non-renew: cancel a subscription at the end of its current period.

    Marks ``cancel_at_period_end=true`` immediately; the subscriber keeps
    access until ``current_period_end``, after which a scheduled task flips
    the status to ``canceled``. Only the subscriber can cancel their own
    subscription.
    """
    subscription = db.get(Subscription, payload.subscription_id)
    if subscription is None or subscription.subscriber_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )
    if subscription.status in (SubscriptionStatus.canceled, SubscriptionStatus.expired):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Subscription is already canceled",
        )
    service = SubscriptionService(db)
    return service.cancel_at_period_end(subscription)
