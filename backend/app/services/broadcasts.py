"""Broadcast business logic: one-time paid unlocks for creator broadcasts.

A **paid broadcast** is a post with ``broadcast_price_cents`` set: it reaches
all subscribers as a locked preview, and each subscriber pays a one-time price
to unlock full media access. ``BroadcastService`` owns the unlock lifecycle —
the one-time charge through the ``PaymentProvider`` abstraction (entirely
separate from the monthly subscription charge) plus the ``PaidUnlock`` record —
and the lock/unlock queries used by the feed and the media endpoint.

Refunds revoke access: a verified ``payment.refunded`` webhook
(:meth:`handle_refunded`) stamps ``PaidUnlock.refunded_at``, after which the
broadcast is locked again until the subscriber re-purchases (the same row is
reactivated in place). The provider is injected (constructor), defaulting to
the configured one from ``get_payment_provider``; tests inject the mock/fake
provider directly (same pattern as ``SubscriptionService``).
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..models import PaidUnlock, Payment, Post, ProcessedWebhookEvent
from ..payments import (
    ChargeRequest,
    WebhookEvent,
    WebhookEventType,
    get_payment_provider,
)

logger = structlog.get_logger()


class BroadcastError(Exception):
    """Base class for broadcast unlock failures."""


class BroadcastNotPaidError(BroadcastError):
    """Raised when unlocking a post that isn't a paid broadcast."""


class PaymentFailedError(BroadcastError):
    """Raised when the one-time charge did not succeed — no unlock is granted."""


class BroadcastService:
    def __init__(self, db: Session, provider=None) -> None:
        self.db = db
        self._provider = provider

    @property
    def provider(self):
        """The gateway provider, resolved lazily on first use."""
        if self._provider is None:
            self._provider = get_payment_provider(settings)
        return self._provider

    # ------------------------------------------------------------------ #
    # Lock/unlock state
    # ------------------------------------------------------------------ #

    def get_unlock(self, subscriber_id: int, post_id: int) -> PaidUnlock | None:
        """The subscriber's *active* unlock row for a post, or None while locked.

        A row is active only once **paid** (``paid_at`` set) and not refunded —
        a pending row (created with a checkout url but no payment yet) is not
        an unlock.
        """
        return self.db.scalar(
            select(PaidUnlock).where(
                PaidUnlock.subscriber_id == subscriber_id,
                PaidUnlock.post_id == post_id,
                PaidUnlock.paid_at.is_not(None),
                PaidUnlock.refunded_at.is_(None),
            )
        )

    def is_unlocked(self, subscriber_id: int, post_id: int) -> bool:
        """True if the subscriber paid and the unlock is still in force."""
        return self.get_unlock(subscriber_id, post_id) is not None

    def unlocked_post_ids(self, subscriber_id: int, post_ids: list[int]) -> set[int]:
        """The subset of ``post_ids`` the subscriber has active unlocks (one query)."""
        if not post_ids:
            return set()
        rows = self.db.scalars(
            select(PaidUnlock.post_id).where(
                PaidUnlock.subscriber_id == subscriber_id,
                PaidUnlock.post_id.in_(post_ids),
                PaidUnlock.paid_at.is_not(None),
                PaidUnlock.refunded_at.is_(None),
            )
        ).all()
        return set(rows)

    # ------------------------------------------------------------------ #
    # Unlock flow
    # ------------------------------------------------------------------ #

    def create_unlock(
        self,
        subscriber_id: int,
        post: Post,
        *,
        success_url: str | None = None,
        cancel_url: str | None = None,
    ) -> tuple[PaidUnlock, bool, str | None]:
        """Create (or re-surface) a hosted payment link for a paid broadcast.

        Returns ``(unlock, created, checkout_url)``:

        - an **active** existing unlock returns ``(row, False, None)`` — the
          subscriber already paid, no new checkout;
        - a **pending** row (created earlier, payment not completed) returns
          ``(row, False, row.checkout_url)`` — the same hosted link is
          re-surfaced so the subscriber can continue paying;
        - otherwise a pending row is created with the provider's hosted
          one-time payment link and ``(row, True, checkout_url)`` is returned.

        The payment is **not** charged synchronously — the subscriber pays on
        the gateway's page and the ``payment.succeeded`` webhook activates the
        unlock (:meth:`handle_paid`). ``success_url``/``cancel_url`` are the
        gateway return urls (the subscriber's current page — where they land
        after paying or backing out). Raises :class:`BroadcastNotPaidError`
        for a regular post.
        """
        row = self.db.scalar(
            select(PaidUnlock).where(
                PaidUnlock.subscriber_id == subscriber_id,
                PaidUnlock.post_id == post.id,
            )
        )
        if row is not None:
            if row.refunded_at is None and row.paid_at is not None:
                return row, False, None  # already paid
            if row.refunded_at is None and row.checkout_url:
                return row, False, row.checkout_url  # still pending

        if post.broadcast_price_cents is None:
            raise BroadcastNotPaidError("This post is not a paid broadcast")

        result = self.provider.create_one_time_link(
            ChargeRequest(
                amount_cents=post.broadcast_price_cents,
                currency="usd",
                description=f"Unlock broadcast post {post.id}",
                metadata={
                    "subscriber_id": str(subscriber_id),
                    "post_id": str(post.id),
                },
                success_url=success_url,
                cancel_url=cancel_url,
            )
        )

        if row is not None:
            # Re-purchase after a refund: reactivate the same row in place.
            row.payment_provider = self.provider.name
            row.external_ref = result.external_ref
            row.checkout_url = result.checkout_url
            row.paid_at = None
            row.refunded_at = None
            unlock = row
        else:
            unlock = PaidUnlock(
                subscriber_id=subscriber_id,
                post_id=post.id,
                payment_provider=self.provider.name,
                external_ref=result.external_ref,
                checkout_url=result.checkout_url,
            )
            self.db.add(unlock)
        try:
            self.db.commit()
        except IntegrityError:
            # A concurrent first-time unlock won the row race (unique
            # constraint) — return their row (its checkout url if still
            # pending).
            self.db.rollback()
            existing = self.db.scalar(
                select(PaidUnlock).where(
                    PaidUnlock.subscriber_id == subscriber_id,
                    PaidUnlock.post_id == post.id,
                )
            )
            if existing is not None:
                return existing, False, existing.checkout_url
            raise
        self.db.refresh(unlock)
        logger.info(
            "paid_unlock_checkout_created",
            subscriber_id=subscriber_id,
            post_id=post.id,
            amount_cents=post.broadcast_price_cents,
            external_ref=result.external_ref,
            reactivated=(row is not None),
        )
        return unlock, True, result.checkout_url

    def find_by_ref(self, external_ref: str | None) -> PaidUnlock | None:
        """The unlock row a payment event refers to (any state, not refunded).

        Used by the webhook dispatcher to route one-time payment events to the
        broadcast flow instead of the subscription flow (a payment-link event's
        external ref is the link id stored on the pending unlock).
        """
        if not external_ref:
            return None
        return self.db.scalar(
            select(PaidUnlock).where(
                PaidUnlock.external_ref == external_ref,
                PaidUnlock.payment_provider == self.provider.name,
                PaidUnlock.refunded_at.is_(None),
            )
        )

    def handle_paid(self, event: WebhookEvent) -> WebhookEvent:
        """Activate the unlock a completed payment webhook refers to.

        Finds the ``PaidUnlock`` by the event's external ref (the hosted link
        id stored at checkout creation), stamps ``paid_at``, clears the
        checkout url and records the completed ``Payment`` in the revenue
        ledger — all in one transaction with the idempotency marker. A
        ``payment.failed`` event for a pending unlock is a no-op (the row stays
        pending; the subscriber can retry the same checkout link).
        """
        if event.event_type != WebhookEventType.payment_succeeded:
            # A failed payment leaves the unlock pending (retryable) — ack
            # without state change.
            return self._mark_processed(event)

        unlock = self.find_by_ref(event.external_ref)
        if unlock is None:
            logger.debug(
                "paid webhook: no matching paid unlock",
                provider=event.provider,
                external_ref=event.external_ref,
            )
            return event

        already_paid = unlock.paid_at is not None
        if not already_paid:
            unlock.paid_at = datetime.now(timezone.utc)
            unlock.checkout_url = None
            self.db.add(
                Payment(
                    creator_id=unlock.post.creator_id if unlock.post is not None else None,
                    subscriber_id=unlock.subscriber_id,
                    kind="unlock",
                    amount_cents=(
                        unlock.post.broadcast_price_cents
                        if unlock.post is not None and unlock.post.broadcast_price_cents is not None
                        else 0
                    ),
                    status="completed",
                    payment_provider=event.provider,
                    external_ref=event.external_ref or unlock.external_ref,
                    post_id=unlock.post_id,
                )
            )
        return self._mark_processed(event)

    def _mark_processed(self, event: WebhookEvent) -> WebhookEvent:
        """Ack an event through the idempotency ledger (one transaction)."""
        if event.id is None:
            return event
        if self._is_processed(event.provider, event.id):
            event.duplicate = True
            return event
        self.db.add(
            ProcessedWebhookEvent(provider=event.provider, event_id=event.id)
        )
        try:
            self.db.commit()
        except IntegrityError:
            # A concurrent delivery of the same event id committed first.
            self.db.rollback()
            event.duplicate = True
        return event

    # ------------------------------------------------------------------ #
    # Refund webhooks (access revocation)
    # ------------------------------------------------------------------ #

    def handle_refunded(self, event: WebhookEvent) -> WebhookEvent:
        """Reconcile a verified ``payment.refunded`` webhook: revoke the unlock.

        Stamps ``refunded_at`` on the matching ``PaidUnlock`` row (by external
        ref first, then by the charge metadata we stamped), so the broadcast
        locks again for that subscriber. Idempotent per provider event id (same
        ledger as subscriptions); an event with no matching unlock is a no-op
        and is deliberately NOT marked processed, so a later redelivery can
        still match once the row exists.
        """
        if event.id and self._is_processed(event.provider, event.id):
            event.duplicate = True
            return event

        unlock = self._unlock_from_event(event)
        if unlock is None:
            logger.debug(
                "refund webhook: no matching paid unlock",
                provider=event.provider,
                external_ref=event.external_ref,
            )
            return event

        unlock.refunded_at = datetime.now(timezone.utc)
        unlock.paid_at = None
        # Revenue ledger: mark the matching charge refunded so it drops out of
        # the revenue totals. Ref first (the exact charge), then the metadata
        # fallback for providers whose refunds carry a foreign id (PayPal
        # capture ids) — same accepted edge as the PaidUnlock matching.
        payment = self.db.scalar(
            select(Payment).where(
                Payment.external_ref == event.external_ref,
                Payment.status == "completed",
            )
        )
        if payment is None:
            meta = event.metadata
            try:
                payment = self.db.scalar(
                    select(Payment)
                    .where(
                        Payment.kind == "unlock",
                        Payment.subscriber_id == int(meta["subscriber_id"]),
                        Payment.post_id == int(meta["post_id"]),
                        Payment.status == "completed",
                    )
                    .order_by(Payment.id)
                )
            except (KeyError, TypeError, ValueError):
                payment = None
        if payment is not None:
            payment.status = "refunded"
        if event.id is not None:
            self.db.add(
                ProcessedWebhookEvent(provider=event.provider, event_id=event.id)
            )
        try:
            self.db.commit()
        except IntegrityError:
            # A concurrent delivery of the same event id committed first (unique
            # ledger constraint) — ack as duplicate; the revocation is identical.
            self.db.rollback()
            event.duplicate = True
            return event
        logger.info(
            "paid_unlock_refunded",
            subscriber_id=unlock.subscriber_id,
            post_id=unlock.post_id,
            external_ref=unlock.external_ref,
        )
        return event

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _unlock_from_event(self, event: WebhookEvent) -> PaidUnlock | None:
        """Find the PaidUnlock a refund event refers to, by ref then metadata.

        Known edge (accepted): after a refund and a re-purchase, a late refund
        event for the *old* charge carrying a **new** event id would miss the
        ref lookup (the row now stores the new charge's ref) and could match by
        metadata, wrongly revoking the active re-purchase. Requires an out-of-
        order provider delivery with a distinct event id — extremely unlikely.
        """
        if event.external_ref:
            unlock = self.db.scalar(
                select(PaidUnlock).where(
                    PaidUnlock.external_ref == event.external_ref,
                    PaidUnlock.payment_provider == event.provider,
                    # A refund refers to a charge — only *paid* rows can be
                    # refunded (a pending checkout refund is not a thing).
                    PaidUnlock.paid_at.is_not(None),
                )
            )
            if unlock is not None:
                return unlock
        # Provider idiosyncrasies: PayPal refunds carry the *capture* id, not
        # the order id we stored. Fall back to the charge metadata we stamped
        # (subscriber_id, post_id) at charge time.
        meta = event.metadata
        subscriber_id = meta.get("subscriber_id")
        post_id = meta.get("post_id")
        if subscriber_id and post_id:
            try:
                return self.db.scalar(
                    select(PaidUnlock).where(
                        PaidUnlock.subscriber_id == int(subscriber_id),
                        PaidUnlock.post_id == int(post_id),
                        PaidUnlock.payment_provider == event.provider,
                    )
                )
            except (TypeError, ValueError):
                return None
        return None

    def _is_processed(self, provider: str, event_id: str) -> bool:
        """True if the (provider, event id) pair was already processed.

        Mirrors ``SubscriptionService._is_processed`` — same ledger table, so a
        refund event and a subscription event can never collide on an id.
        """
        return (
            self.db.scalar(
                select(ProcessedWebhookEvent.id).where(
                    ProcessedWebhookEvent.provider == provider,
                    ProcessedWebhookEvent.event_id == event_id,
                )
            )
            is not None
        )
