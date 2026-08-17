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

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..access import is_blocked, resolve_viewer_context
from ..database import get_db
from ..deps import get_current_user
from ..gateways import is_config_complete
from ..models import Subscription, SubscriptionStatus, User, UserRole
from ..payments import (
    PaymentProvider,
    PaymentProviderError,
    ProviderConfigurationError,
)
from ..payments.factory import build_provider_from_config, resolve_plan_id
from ..schemas import (
    CancelRequest,
    MySubscriptionOut,
    MySubscriptionsOut,
    SubscribeRequest,
    SubscribeResponse,
    SubscribeStatusOut,
    SubscriptionOut,
)
from ..services.gateways import enabled_configured_gateways, get_gateway_row
from ..services.subscriptions import SubscriptionService, tier_price_cents_for

router = APIRouter(tags=["subscriptions"])

logger = structlog.get_logger()

# Subscriber-facing message when a creator's payment gateway is broken or
# misconfigured. The technical reason is logged server-side (the subscriber
# can't fix a gateway configuration) — end users only ever see this generic
# line, never operator instructions like "run bootstrap_paypal".
_PAYMENT_UNAVAILABLE = (
    "This creator's payment method is temporarily unavailable. "
    "Please try again later."
)


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
    # A blocked user is rejected up front — before gateway resolution — so the
    # answer is always the clear 403 (never a confusing "no gateway" 400).
    if is_blocked(db, creator.id, user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You have been blocked by this creator",
        )

    # Resolve the payment gateway: the client may pick one of the creator's
    # enabled gateways explicitly; otherwise a single enabled+configured
    # gateway is used (ambiguous/absent -> 400 so the checkout UI can react).
    gateway, provider, plan_id = _resolve_gateway(db, creator.id, payload.provider)

    # Consent gate: the subscriber must confirm they are 18+ and accept the
    # creator's Terms of Service **before any payment is started** (the hosted
    # checkout is only created below once consent is verified). The confirmed
    # state is recorded on the row as the consent audit trail (age_confirmed +
    # tos_accepted_at), written in the same transaction as the pending row.
    if not payload.accepted_tos or not payload.age_confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "You must confirm you are 18 or older and accept the Terms of "
                "Service and Privacy Policy before subscribing."
            ),
        )

    service = SubscriptionService(db, provider=provider)
    try:
        subscription = service.create_subscription(
            subscriber_id=user.id,
            creator_id=creator.id,
            plan_id=plan_id,
            success_url=payload.success_url,
            cancel_url=payload.cancel_url,
            age_confirmed=payload.age_confirmed,
            tos_accepted_at=datetime.now(timezone.utc),
            # The creator's own monthly price (or the platform default) —
            # charged by amount-based gateways and snapshotted onto the row
            # for the revenue ledger.
            amount_cents=tier_price_cents_for(creator.creator_profile),
        )
    except (ProviderConfigurationError, PaymentProviderError) as exc:
        # The gateway refused the checkout (e.g. a misconfigured plan id, or
        # PayPal rejecting the request). That's a creator/server-side problem
        # — log the actionable detail and answer the subscriber with a
        # generic message, never the operator-facing reason.
        logger.error(
            "subscribe failed at gateway",
            gateway=gateway,
            creator_id=creator.id,
            error=str(exc),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_PAYMENT_UNAVAILABLE,
        )
    return SubscribeResponse(
        subscription=SubscriptionOut.model_validate(subscription),
        checkout_url=subscription.checkout_url,
        status=subscription.status.value,
    )


@router.get("/me/subscriptions", response_model=MySubscriptionsOut)
def my_subscriptions(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every subscription the authenticated user holds (newest first).

    Powers the subscriber profile page: which creators they follow, each
    row's status, and the **days left** in the current billing period (from
    ``current_period_end``) so the page can show "N days left" per active
    subscription.
    """
    rows = db.scalars(
        select(Subscription)
        .where(Subscription.subscriber_id == user.id)
        .order_by(Subscription.created_at.desc())
    ).all()
    items: list[MySubscriptionOut] = []
    for sub in rows:
        creator = db.get(User, sub.creator_id)
        profile = creator.creator_profile if creator is not None else None
        items.append(
            MySubscriptionOut(
                subscription_id=sub.id,
                creator_id=sub.creator_id,
                creator_username=creator.username if creator is not None else None,
                creator_display_name=(
                    profile.display_name if profile is not None else None
                ),
                status=sub.status.value,
                current_period_start=sub.current_period_start,
                current_period_end=sub.current_period_end,
                cancel_at_period_end=sub.cancel_at_period_end,
                payment_provider=sub.payment_provider,
                created_at=sub.created_at,
                days_left=_days_left(sub),
            )
        )
    return MySubscriptionsOut(items=items)


def _days_left(sub: Subscription) -> int | None:
    """Days until ``current_period_end`` for an active/trialing row.

    Rounds **up** so "9 days left" stays accurate across the day boundary
    (9.0 days and 8.1 days both read as 9) and never shows 0 while access
    still exists.
    """
    if sub.status not in (SubscriptionStatus.active, SubscriptionStatus.trialing):
        return None
    if sub.current_period_end is None:
        return None
    period_end = sub.current_period_end
    if period_end.tzinfo is None:
        # SQLite returns naive datetimes; treat them as UTC.
        period_end = period_end.replace(tzinfo=timezone.utc)
    remaining = period_end - datetime.now(timezone.utc)
    return max((remaining.total_seconds() + 86399) // 86400, 0)


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
        # The creator's own monthly price — the checkout form displays this
        # exact amount (falling back to the platform default when unset).
        tier_price_cents=tier_price_cents_for(creator.creator_profile),
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
        # Resolved inside the try so a plan id that can't belong to the gateway
        # (e.g. the Stripe placeholder default sent to PayPal) fails fast
        # instead of a cryptic gateway rejection or a 500.
        plan_id = resolve_plan_id(gateway, config)
    except ProviderConfigurationError as exc:
        # Operator-facing detail (billing-plan setup instructions) never
        # reaches the subscriber — log it and answer generically.
        logger.error(
            "subscribe gateway misconfigured",
            gateway=gateway,
            creator_id=creator_id,
            error=str(exc),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_PAYMENT_UNAVAILABLE,
        )
    return gateway, provider, plan_id
