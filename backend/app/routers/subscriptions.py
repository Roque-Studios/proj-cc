"""Subscription endpoints.

- ``POST /subscribe`` starts a subscription to a creator at the monthly tier,
  opening a hosted checkout with **one of the creator's enabled gateways** (see
  ``CreatorGatewayConfig``). The row is created as ``incomplete`` (pending
  payment); a successful payment webhook activates it, a failed one leaves it
  incomplete.
- ``POST /cancel`` sets non-renew on a subscription: access persists until
  ``current_period_end``, then a scheduled task expires it to ``canceled``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..access import resolve_viewer_context
from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..gateways import is_config_complete
from ..models import Subscription, SubscriptionStatus, User, UserRole
from ..payments import PaymentProvider, ProviderConfigurationError
from ..payments.factory import build_provider_from_config, resolve_plan_id
from ..schemas import (
    CancelRequest,
    SubscribeRequest,
    SubscribeResponse,
    SubscribeStatusOut,
    SubscriptionOut,
)
from ..services.gateways import enabled_configured_gateways, get_gateway_row
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

    # Resolve the payment gateway: the client may pick one of the creator's
    # enabled gateways explicitly; otherwise a single enabled+configured
    # gateway is used (ambiguous/absent -> 400 so the checkout UI can react).
    gateway, provider, plan_id = _resolve_gateway(db, creator.id, payload.provider)
    service = SubscriptionService(db, provider=provider)
    subscription = service.create_subscription(
        subscriber_id=user.id,
        creator_id=creator.id,
        plan_id=plan_id,
        success_url=payload.success_url,
        cancel_url=payload.cancel_url,
    )
    return SubscribeResponse(
        subscription=SubscriptionOut.model_validate(subscription),
        checkout_url=subscription.checkout_url,
        status=subscription.status.value,
    )


@router.get("/subscribe/status", response_model=SubscribeStatusOut)
def subscribe_status(
    request: Request,
    creator_id: int = Query(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The viewer's subscription state for a creator — checkout reconciliation.

    Authenticated only. Returns the viewer's current row for this creator (any
    status) plus their access level. The checkout UI polls this after the
    hosted payment redirect to reconcile the final state: a row that went
    ``active``/``trialing`` means the webhook landed; one still ``incomplete``
    means the payment didn't complete (or the webhook is still in flight).
    """
    creator = db.get(User, creator_id)
    if creator is None or creator.role != UserRole.creator or not creator.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Creator not found",
        )

    ctx = resolve_viewer_context(request, creator_id, db)
    subscription = None
    if ctx.subscription is not None:
        subscription = ctx.subscription
    else:
        # The resolver only returns active/trialing rows; the checkout also
        # needs the incomplete/past_due row to show a pending payment.
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.subscriber_id == user.id,
                Subscription.creator_id == creator_id,
            )
        )

    return SubscribeStatusOut(
        viewer_level=ctx.level.value,
        subscription=(
            SubscriptionOut.model_validate(subscription) if subscription is not None else None
        ),
        tier_price_cents=settings.SUBSCRIPTION_TIER_PRICE_CENTS,
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


def _resolve_gateway(
    db: Session,
    creator_id: int,
    provider: str | None,
) -> tuple[str, PaymentProvider, str]:
    """Resolve (gateway name, provider instance, plan id) for a creator.

    Strictly per-creator: the gateway must be one the creator enabled **with a
    complete config** — platform env credentials are never used for checkout.
    """
    requested = provider.strip().lower() if provider else ""
    if requested:
        row = get_gateway_row(db, creator_id, requested)
        if row is None or not row.enabled or not is_config_complete(requested, row.config):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Gateway '{requested}' is not enabled for this creator "
                    "(or its configuration is incomplete)"
                ),
            )
        gateway = requested
        config = row.config
    else:
        enabled = enabled_configured_gateways(db, creator_id)
        if not enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Creator has no payment gateway enabled",
            )
        if len(enabled) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Creator has multiple payment gateways enabled — "
                    "specify which one to use"
                ),
            )
        gateway, row = enabled[0]
        config = row.config
    try:
        provider = build_provider_from_config(gateway, config)
    except ProviderConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Gateway '{gateway}' configuration is invalid: {exc}",
        )
    return gateway, provider, resolve_plan_id(gateway, config)
