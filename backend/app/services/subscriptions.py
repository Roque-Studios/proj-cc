"""Subscription business logic.

``SubscriptionService`` is the single place that orchestrates the subscription
lifecycle. It depends **only** on the ``PaymentProvider`` interface — never on a
specific gateway — so switching from Stripe to PayPal (or adding a new gateway)
requires zero changes to this file or the models.

The provider is injected (constructor), defaulting to the configured one from
``get_payment_provider``; tests inject the mock/fake provider directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Mapping

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Payment,
    ProcessedWebhookEvent,
    Subscription,
    SubscriptionStatus,
    User,
)
from ..notifications import enqueue_payment_failed_notification
from ..payments import (
    ChargeRequest,
    PaymentProvider,
    SubscriptionIntent,
    WebhookEvent,
    WebhookEventType,
    get_payment_provider,
)

logger = structlog.get_logger()

# The monthly tier's access window. Providers whose events don't report a
# billing period (Wompi payment links are flat transaction payloads) get their
# period backfilled from the paid moment — same length the mock/stripe
# providers use when creating the subscription.
_TIER_PERIOD_DAYS = 30

def tier_price_cents_for(profile) -> int:
    """The effective monthly subscription price for a creator's profile.

    The creator's own ``tier_price_cents`` wins when set; otherwise the
    platform default ``settings.SUBSCRIPTION_TIER_PRICE_CENTS``. Shared by the
    subscribe flow (amount + ledger), the checkout status endpoint (display)
    and the public landing payload.
    """
    if profile is not None and profile.tier_price_cents:
        return profile.tier_price_cents
    return settings.SUBSCRIPTION_TIER_PRICE_CENTS


# Normalized provider status string -> our model status.
_STATUS_MAP = {
    "active": SubscriptionStatus.active,
    "trialing": SubscriptionStatus.trialing,
    "incomplete": SubscriptionStatus.incomplete,
    "past_due": SubscriptionStatus.past_due,
    "canceled": SubscriptionStatus.canceled,
    "expired": SubscriptionStatus.expired,
}

# Statuses an email-fallback webhook match may still reconcile (a canceled or
# expired row must never be resurrected by a late event).
_NON_TERMINAL = (
    SubscriptionStatus.active,
    SubscriptionStatus.trialing,
    SubscriptionStatus.past_due,
    SubscriptionStatus.incomplete,
)


class SubscriptionService:
    def __init__(self, db: Session, provider: PaymentProvider | None = None) -> None:
        self.db = db
        self._provider = provider

    @property
    def provider(self) -> PaymentProvider:
        """The gateway provider, resolved lazily on first use.

        Non-payment flows (e.g. the scheduled expiry sweep in the Celery
        worker) never touch the provider, so constructing a gateway client
        there is avoided entirely.
        """
        if self._provider is None:
            self._provider = get_payment_provider(settings)
        return self._provider

    # ------------------------------------------------------------------ #
    # Core flows
    # ------------------------------------------------------------------ #

    def create_subscription(
        self,
        subscriber_id: int,
        creator_id: int,
        plan_id: str,
        success_url: str | None = None,
        cancel_url: str | None = None,
        *,
        age_confirmed: bool = False,
        tos_accepted_at: datetime | None = None,
        amount_cents: int | None = None,
    ) -> Subscription:
        """Start a subscription and persist it as a *pending* (incomplete) row.

        Ensures the subscriber has a customer at the gateway (created lazily
        and cached on ``user.payment_customer_id``), then opens the hosted
        checkout (the pending payment intent) and stores the row as
        ``incomplete`` with the checkout url. Only a successful payment webhook
        (``invoice.paid`` / ``payment.succeeded``) activates it; a failed one
        leaves it ``incomplete``.

        Idempotent: an existing non-terminal subscription (active, trialing,
        past_due, or incomplete) to this creator is returned as-is (with its
        checkout url). A terminal row (canceled/expired) is reactivated in place
        so re-subscribing works against the same (subscriber, creator) pair.
        """
        _PENDING_OR_ACTIVE = (
            SubscriptionStatus.active,
            SubscriptionStatus.trialing,
            SubscriptionStatus.past_due,
            SubscriptionStatus.incomplete,
        )
        existing = self.db.scalar(
            select(Subscription).where(
                Subscription.subscriber_id == subscriber_id,
                Subscription.creator_id == creator_id,
            )
        )
        if existing is not None and existing.status in _PENDING_OR_ACTIVE:
            return existing

        # The consent audit trail: capture the subscriber's 18+ confirmation
        # and Terms-of-Service acceptance at creation/reactivation time (the
        # router enforces the gate; the service just records what was
        # confirmed). An already-pending/active row keeps its original consent.
        subscriber = self.db.get(User, subscriber_id)
        if subscriber is None:
            raise ValueError(f"Unknown subscriber: {subscriber_id}")
        creator = self.db.get(User, creator_id)

        customer_ref = self._get_or_create_customer(subscriber)
        intent = SubscriptionIntent(
            plan_id=plan_id,
            subscriber_email=subscriber.email,
            subscriber_name=subscriber.username,
            customer_ref=customer_ref,
            # The agreed monthly price (the creator's tier price) — Wompi
            # payment links charge this exact amount; the amount is also
            # snapshotted onto the row for the revenue ledger.
            amount_cents=amount_cents,
            metadata={
                "subscriber_id": str(subscriber_id),
                "creator_id": str(creator_id),
                # The creator's username — Wompi's ``nombreProducto`` becomes
                # "subscription to <username>" (the creator tag shown on the
                # hosted page / Wompi dashboard).
                "creator_username": creator.username if creator else None,
            },
            success_url=success_url,
            cancel_url=cancel_url,
        )
        result = self.provider.create_subscription(intent)

        if existing is not None:
            # Reactivate the terminal row (unique constraint satisfied).
            subscription = existing
            subscription.status = SubscriptionStatus.incomplete
            subscription.current_period_start = result.current_period_start
            subscription.current_period_end = result.current_period_end
            subscription.payment_provider = self.provider.name
            subscription.external_ref = result.external_ref
            subscription.checkout_url = result.checkout_url
            if tos_accepted_at is not None:
                subscription.age_confirmed = age_confirmed
                subscription.tos_accepted_at = tos_accepted_at
            if amount_cents is not None:
                subscription.tier_price_cents = amount_cents
        else:
            subscription = Subscription(
                subscriber_id=subscriber_id,
                creator_id=creator_id,
                status=SubscriptionStatus.incomplete,
                current_period_start=result.current_period_start,
                current_period_end=result.current_period_end,
                payment_provider=self.provider.name,
                external_ref=result.external_ref,
                checkout_url=result.checkout_url,
                age_confirmed=age_confirmed,
                tos_accepted_at=tos_accepted_at,
                tier_price_cents=amount_cents,
            )
            self.db.add(subscription)
        self.db.commit()
        self.db.refresh(subscription)
        return subscription

    def cancel_subscription(self, subscription: Subscription) -> Subscription:
        """Cancel immediately at the provider, then mark the local row canceled."""
        if subscription.external_ref:
            self.provider.cancel_subscription(subscription.external_ref)
        subscription.status = SubscriptionStatus.canceled
        self.db.commit()
        self.db.refresh(subscription)
        return subscription

    def cancel_at_period_end(self, subscription: Subscription) -> Subscription:
        """Non-renew: set the cancel-at-period-end flag (access persists).

        The subscription stays active (subscriber keeps access) until
        ``current_period_end``; a scheduled Celery task then expires it to
        ``canceled`` (see :meth:`expire_canceled_subscriptions`).
        """
        subscription.cancel_at_period_end = True
        if subscription.external_ref:
            self.provider.cancel_at_period_end(subscription.external_ref)
        self.db.commit()
        self.db.refresh(subscription)
        return subscription

    def expire_canceled_subscriptions(
        self, now: datetime | None = None
    ) -> int:
        """Expire non-renewing subscriptions whose period has ended.

        Flips active/trialing subscriptions flagged ``cancel_at_period_end``
        with ``current_period_end <= now`` to ``canceled`` and clears the flag.
        Returns the number of subscriptions expired.

        ``now`` is injectable for tests (time-travel); defaults to the real
        clock. Intended to be driven by a scheduled Celery beat task.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        rows = self.db.scalars(
            select(Subscription).where(
                Subscription.cancel_at_period_end.is_(True),
                Subscription.status.in_(
                    (
                        SubscriptionStatus.active,
                        SubscriptionStatus.trialing,
                        SubscriptionStatus.past_due,
                    )
                ),
                Subscription.current_period_end.is_not(None),
                Subscription.current_period_end <= now,
            )
        ).all()
        for subscription in rows:
            subscription.status = SubscriptionStatus.canceled
            subscription.cancel_at_period_end = False
        if rows:
            self.db.commit()
        return len(rows)

    def handle_webhook(
        self,
        body: bytes,
        headers: Mapping[str, str],
        *,
        event: WebhookEvent | None = None,
    ) -> WebhookEvent:
        """Verify a webhook and reconcile it with the local subscription.

        ``event`` may carry a **pre-verified** ``WebhookEvent`` (the webhook
        router verifies once and dispatches by type); when omitted the body is
        verified here, so direct callers are unchanged.

        Processing is idempotent per provider event: an event id already in the
        ``processed_webhook_event`` ledger is a duplicate delivery (provider
        retry) and is acknowledged without re-applying changes — a renewal
        webhook never double-processes and failure notifications fire at most
        once per episode. Raises ``WebhookVerificationError`` for bad
        signatures.
        """
        if event is None:
            event = self.provider.verify_webhook(body, headers)
        if event.external_ref is None:
            return event

        # Idempotency: skip redeliveries of an already-processed event. Only
        # *verified* events reach this point, so a forged signature can never
        # pollute the ledger.
        if event.id and self._is_processed(event.provider, event.id):
            event.duplicate = True
            return event

        subscription = self.db.scalar(
            select(Subscription).where(
                Subscription.external_ref == event.external_ref,
                Subscription.payment_provider == event.provider,
            )
        )
        ref_adopted = False
        if subscription is None:
            # A checkout.session.completed event carries the *subscription* id
            # while our row was created with the *checkout session* id. Look the
            # row up by that session id and adopt the real subscription id.
            session_id = event.metadata.get("checkout_session_id")
            if session_id:
                subscription = self.db.scalar(
                    select(Subscription).where(
                        Subscription.external_ref == session_id,
                        Subscription.payment_provider == event.provider,
                    )
                )
                if subscription is not None and event.external_ref != session_id:
                    subscription.external_ref = event.external_ref
                    ref_adopted = True
        if (
            subscription is None
            and event.customer_email
            and event.recurring
        ):
            # Gateways whose subscription-charge events don't reference the ref
            # we stored (e.g. Wompi payment-link charges carry the merchant ref
            # + payer email, not the link id) match by the payer email instead:
            # the user's latest non-terminal row for this provider. The event
            # ref is NOT adopted — ``external_ref`` stays the gateway resource
            # we created (the Wompi link id). Gated on ``event.recurring``: a
            # provider's one-time purchase event (same email, no subscription
            # ref) must never be reconciled against a subscription row — that
            # would record a spurious monthly payment in the revenue ledger for
            # what was a one-time unlock. Safe: only signature-verified events
            # reach this point. Limitation (accepted, solo platform): with
            # several non-terminal rows for one email the latest by id is
            # chosen — unless the event pins the creator (see below).
            user = self.db.scalar(
                select(User).where(User.email == event.customer_email)
            )
            if user is not None:
                query = select(Subscription).where(
                    Subscription.subscriber_id == user.id,
                    Subscription.payment_provider == event.provider,
                    Subscription.status.in_(_NON_TERMINAL),
                )
                # A Wompi payment-link event echoes the merchant reference
                # (``identificadorEnlaceComercio`` = the creator id): pin the
                # match to that creator so a subscriber with rows for several
                # creators is reconciled to the one actually charged.
                creator_ref = event.metadata.get("creator_id")
                if creator_ref:
                    try:
                        query = query.where(
                            Subscription.creator_id == int(creator_ref)
                        )
                    except (TypeError, ValueError):
                        pass  # malformed ref — fall back to email-only matching
                subscription = self.db.scalar(
                    query.order_by(Subscription.id.desc())
                )
        if subscription is None:
            # Unknown subscription (e.g. webhook arriving before our row, or a
            # different environment) — nothing to reconcile, and deliberately
            # NOT marked processed so a later redelivery can still match.
            return event

        previous_status = subscription.status
        new_status = self._status_from_event(event, previous_status)
        if new_status is not None:
            subscription.status = new_status
            if new_status == SubscriptionStatus.active:
                # Payment succeeded: no longer pending — clear the checkout url.
                subscription.checkout_url = None
        # Apply the billing period reported by the event (e.g. from Stripe
        # invoice events) so current_period_end tracks the paid period.
        period_supplied = event.period_start is not None or event.period_end is not None
        if event.period_start is not None:
            subscription.current_period_start = event.period_start
        if event.period_end is not None:
            subscription.current_period_end = event.period_end
        # Providers whose events never carry a billing period (Wompi payment
        # links are flat transaction payloads) leave the period unset even
        # after a successful charge — the monthly tier's access window is then
        # backfilled from the paid moment so days-left and expiry still work.
        # A provider-supplied period above always wins (this only fills a gap).
        period_backfilled = False
        if (
            subscription.status in (SubscriptionStatus.active, SubscriptionStatus.trialing)
            and subscription.current_period_end is None
        ):
            # First activation (or a row that lost its window): fresh month.
            paid_at = datetime.now(timezone.utc)
            if subscription.current_period_start is None:
                subscription.current_period_start = paid_at
            subscription.current_period_end = paid_at + timedelta(days=_TIER_PERIOD_DAYS)
            period_backfilled = True
        elif (
            not period_supplied
            and new_status == SubscriptionStatus.active
            and previous_status == SubscriptionStatus.active
            and event.event_type == WebhookEventType.payment_succeeded
            and subscription.current_period_end is not None
        ):
            # On-time renewal from a provider with no period data (a second
            # Wompi payment-link payment while access persists): each payment
            # buys one month — extend the current window (or start a fresh one
            # when it already lapsed). Idempotent redeliveries are deduped by
            # the event-id ledger, so this can't double-extend.
            period_end = subscription.current_period_end
            if period_end.tzinfo is None:
                period_end = period_end.replace(tzinfo=timezone.utc)
            paid_at = datetime.now(timezone.utc)
            subscription.current_period_end = max(period_end, paid_at) + timedelta(
                days=_TIER_PERIOD_DAYS
            )
            period_backfilled = True

        # Revenue ledger: every *completed monthly payment* records one tier
        # payment for the creator. The money signal is ``payment_succeeded``
        # (Stripe invoice.paid / PayPal PAYMENT.SALE.COMPLETED / Wompi APROBADA
        # all normalize to it, activation and renewals alike). The mock dev
        # provider never sends a payment event for its hosted activation, so
        # its subscription-created/updated transition into ``active`` counts as
        # the first month (Stripe also sends a subscription.created on checkout
        # completion, but its invoice.paid already records the charge — so the
        # transition rule stays scoped to mock to avoid double-counting).
        # Written in the same transaction as the reconciliation: a duplicate
        # event (IntegrityError rollback) records nothing.
        payment_completed = event.event_type == WebhookEventType.payment_succeeded or (
            event.provider == "mock"
            and event.event_type
            in (WebhookEventType.subscription_created, WebhookEventType.subscription_updated)
            and new_status == SubscriptionStatus.active
            and previous_status != SubscriptionStatus.active
        )
        if payment_completed:
            self.db.add(
                Payment(
                    creator_id=subscription.creator_id,
                    subscriber_id=subscription.subscriber_id,
                    kind="subscription",
                    # The price snapshot on the row — the amount the subscriber
                    # actually agreed to pay (legacy rows fall back to the
                    # settings default).
                    amount_cents=subscription.tier_price_cents
                    or settings.SUBSCRIPTION_TIER_PRICE_CENTS,
                    status="completed",
                    payment_provider=event.provider,
                    external_ref=event.external_ref,
                )
            )

        # Persist the reconciliation and the idempotency marker atomically:
        # either both land (the event is acknowledged exactly once) or neither
        # does (the provider retries — safe, because the status transitions
        # themselves are idempotent).
        changed = (
            ref_adopted
            or new_status is not None
            or event.period_end is not None
            or period_backfilled
        )
        if event.id is not None:
            self.db.add(
                ProcessedWebhookEvent(
                    provider=event.provider,
                    event_id=event.id,
                )
            )
            changed = True
        if changed:
            try:
                self.db.commit()
            except IntegrityError:
                # A concurrent delivery of the same event id committed first
                # (unique ledger constraint) — our copy is a duplicate. The
                # reconciliation is identical either way, so just ack it.
                self.db.rollback()
                event.duplicate = True
                return event
            self.db.refresh(subscription)
            # Notify only on the *transition* into the past-due grace period;
            # repeated failure events while already past_due don't re-notify.
            if (
                new_status == SubscriptionStatus.past_due
                and previous_status != SubscriptionStatus.past_due
            ):
                try:
                    enqueue_payment_failed_notification(subscription.id)
                except Exception:  # noqa: BLE001 — notification is best-effort
                    logger.exception(
                        "failed to enqueue payment failed notification",
                        subscription_id=subscription.id,
                    )
        return event

    def charge_one_time(
        self,
        subscriber_id: int,
        creator_id: int,
        amount_cents: int,
        currency: str = "usd",
        description: str | None = None,
    ):
        """Charge a one-time amount via the provider (e.g. tips, unlocks).

        Returns the provider's ``ChargeResult``.
        """
        request = ChargeRequest(
            amount_cents=amount_cents,
            currency=currency,
            description=description,
            metadata={
                "subscriber_id": str(subscriber_id),
                "creator_id": str(creator_id),
            },
        )
        return self.provider.charge_one_time(request)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _get_or_create_customer(self, subscriber: User) -> str:
        """Return the subscriber's gateway customer ref, creating + caching it once.

        Note: the check-then-create is not atomic under concurrency — two racing
        requests could each create a customer at the gateway (acceptable for a
        solo platform; the extra customer is harmless and one wins the cache).
        """
        if subscriber.payment_customer_id:
            return subscriber.payment_customer_id
        customer_ref = self.provider.create_customer(
            email=subscriber.email,
            name=subscriber.username,
            metadata={"user_id": str(subscriber.id)},
        )
        subscriber.payment_customer_id = customer_ref
        self.db.commit()
        return customer_ref

    def _is_processed(self, provider: str, event_id: str) -> bool:
        """True if the (provider, event id) pair was already processed."""
        return (
            self.db.scalar(
                select(ProcessedWebhookEvent.id).where(
                    ProcessedWebhookEvent.provider == provider,
                    ProcessedWebhookEvent.event_id == event_id,
                )
            )
            is not None
        )

    def _status_from_event(
        self, event: WebhookEvent, current: SubscriptionStatus
    ) -> SubscriptionStatus | None:
        """Map a normalized webhook event to a target subscription status.

        A ``payment_failed`` event only moves an **active** subscription to
        ``past_due``; a still-pending (``incomplete``) subscription stays
        ``incomplete``, per the subscribe acceptance (failed payment leaves the
        status as incomplete).
        """
        if event.event_type == WebhookEventType.subscription_canceled:
            return SubscriptionStatus.canceled
        if event.event_type == WebhookEventType.payment_failed:
            if current == SubscriptionStatus.incomplete:
                return None  # stays incomplete — no transition
            return SubscriptionStatus.past_due
        if event.event_type == WebhookEventType.payment_succeeded:
            return SubscriptionStatus.active
        if event.event_type in (
            WebhookEventType.subscription_created,
            WebhookEventType.subscription_updated,
        ):
            # Trust the status carried by the provider event when present.
            return _STATUS_MAP.get(event.subscription_status or "")
        return None
