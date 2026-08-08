"""Paid DM content: one-time paid unlocks for messages with media.

A **paid message** is a DM with ``price_cents`` set (the creator sends
exclusive media the recipient pays once to view). ``PaidMessageService`` owns
the unlock lifecycle — the same **hosted checkout + webhook** pattern as
broadcast unlocks (``PaymentProvider.create_one_time_link`` + a
``payment.succeeded`` webhook activating the row) — plus the lock/unlock
queries the chat UI and media endpoint use.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Message, PaidMessageUnlock, Payment, ProcessedWebhookEvent
from ..payments import (
    ChargeRequest,
    WebhookEvent,
    WebhookEventType,
    get_payment_provider,
)

logger = structlog.get_logger()


class PaidMessageNotPaidError(Exception):
    """Raised when unlocking a message that isn't a paid message."""


class PaidMessageService:
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

    def get_unlock(self, subscriber_id: int, message_id: int) -> PaidMessageUnlock | None:
        """The subscriber's *active* unlock row for a message, or None."""
        return self.db.scalar(
            select(PaidMessageUnlock).where(
                PaidMessageUnlock.subscriber_id == subscriber_id,
                PaidMessageUnlock.message_id == message_id,
                PaidMessageUnlock.paid_at.is_not(None),
                PaidMessageUnlock.refunded_at.is_(None),
            )
        )

    def is_unlocked(self, subscriber_id: int, message_id: int) -> bool:
        return self.get_unlock(subscriber_id, message_id) is not None

    def unlocked_message_ids(
        self, subscriber_id: int, message_ids: list[int]
    ) -> set[int]:
        """The subset of ``message_ids`` the subscriber has active unlocks."""
        if not message_ids:
            return set()
        rows = self.db.scalars(
            select(PaidMessageUnlock.message_id).where(
                PaidMessageUnlock.subscriber_id == subscriber_id,
                PaidMessageUnlock.message_id.in_(message_ids),
                PaidMessageUnlock.paid_at.is_not(None),
                PaidMessageUnlock.refunded_at.is_(None),
            )
        ).all()
        return set(rows)

    # ------------------------------------------------------------------ #
    # Unlock flow
    # ------------------------------------------------------------------ #

    def create_unlock(
        self,
        subscriber_id: int,
        message: Message,
        *,
        success_url: str | None = None,
        cancel_url: str | None = None,
    ) -> tuple[PaidMessageUnlock, bool, str | None]:
        """Create (or re-surface) a hosted payment link for a paid message.

        Returns ``(unlock, created, checkout_url)`` — active unlock returns
        ``(row, False, None)``, a pending row re-surfaces its checkout url,
        otherwise a pending row is created with the provider's hosted one-time
        link. The payment completes on the gateway's page; the webhook
        activates the unlock (:meth:`handle_paid`).
        ``success_url``/``cancel_url`` are the gateway return urls.
        """
        row = self.db.scalar(
            select(PaidMessageUnlock).where(
                PaidMessageUnlock.subscriber_id == subscriber_id,
                PaidMessageUnlock.message_id == message.id,
            )
        )
        if row is not None:
            if row.refunded_at is None and row.paid_at is not None:
                return row, False, None  # already paid
            if row.refunded_at is None and row.checkout_url:
                return row, False, row.checkout_url  # still pending

        if message.price_cents is None:
            raise PaidMessageNotPaidError("This message is not a paid message")

        result = self.provider.create_one_time_link(
            ChargeRequest(
                amount_cents=message.price_cents,
                currency="usd",
                description=f"Unlock message {message.id}",
                metadata={
                    "subscriber_id": str(subscriber_id),
                    "message_id": str(message.id),
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
            unlock = PaidMessageUnlock(
                subscriber_id=subscriber_id,
                message_id=message.id,
                payment_provider=self.provider.name,
                external_ref=result.external_ref,
                checkout_url=result.checkout_url,
            )
            self.db.add(unlock)
        try:
            self.db.commit()
        except IntegrityError:
            # A concurrent first-time unlock won the row race — re-read it.
            self.db.rollback()
            existing = self.db.scalar(
                select(PaidMessageUnlock).where(
                    PaidMessageUnlock.subscriber_id == subscriber_id,
                    PaidMessageUnlock.message_id == message.id,
                )
            )
            if existing is not None:
                return existing, False, existing.checkout_url
            raise
        self.db.refresh(unlock)
        logger.info(
            "paid_message_checkout_created",
            subscriber_id=subscriber_id,
            message_id=message.id,
            amount_cents=message.price_cents,
            external_ref=result.external_ref,
        )
        return unlock, True, result.checkout_url

    def find_by_ref(self, external_ref: str | None) -> PaidMessageUnlock | None:
        """The unlock row a payment event refers to (any state, not refunded)."""
        if not external_ref:
            return None
        return self.db.scalar(
            select(PaidMessageUnlock).where(
                PaidMessageUnlock.external_ref == external_ref,
                PaidMessageUnlock.payment_provider == self.provider.name,
                PaidMessageUnlock.refunded_at.is_(None),
            )
        )

    def handle_paid(self, event: WebhookEvent) -> WebhookEvent:
        """Activate the unlock a completed payment webhook refers to."""
        if event.event_type != WebhookEventType.payment_succeeded:
            # A failed payment leaves the unlock pending (retryable) — ack.
            return self._mark_processed(event)

        unlock = self.find_by_ref(event.external_ref)
        if unlock is None:
            logger.debug(
                "paid message webhook: no matching unlock",
                provider=event.provider,
                external_ref=event.external_ref,
            )
            return event

        if unlock.paid_at is None:
            unlock.paid_at = datetime.now(timezone.utc)
            unlock.checkout_url = None
            self.db.add(
                Payment(
                    creator_id=unlock.message.sender_id if unlock.message is not None else None,
                    subscriber_id=unlock.subscriber_id,
                    kind="unlock",
                    amount_cents=(
                        unlock.message.price_cents
                        if unlock.message is not None and unlock.message.price_cents is not None
                        else 0
                    ),
                    status="completed",
                    payment_provider=event.provider,
                    external_ref=event.external_ref or unlock.external_ref,
                    message_id=unlock.message_id,
                )
            )
        return self._mark_processed(event)

    def handle_refunded(self, event: WebhookEvent) -> WebhookEvent:
        """Revoke a paid-message unlock on a verified ``payment.refunded``."""
        if event.id and self._is_processed(event.provider, event.id):
            event.duplicate = True
            return event
        unlock = self.db.scalar(
            select(PaidMessageUnlock).where(
                PaidMessageUnlock.external_ref == event.external_ref,
                PaidMessageUnlock.payment_provider == event.provider,
                PaidMessageUnlock.paid_at.is_not(None),
            )
        )
        if unlock is None:
            return event
        unlock.refunded_at = datetime.now(timezone.utc)
        unlock.paid_at = None
        payment = self.db.scalar(
            select(Payment).where(
                Payment.external_ref == event.external_ref,
                Payment.status == "completed",
                Payment.kind == "unlock",
                Payment.message_id == unlock.message_id,
            )
        )
        if payment is not None:
            payment.status = "refunded"
        return self._mark_processed(event)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _mark_processed(self, event: WebhookEvent) -> WebhookEvent:
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
            self.db.rollback()
            event.duplicate = True
        return event

    def _is_processed(self, provider: str, event_id: str) -> bool:
        return (
            self.db.scalar(
                select(ProcessedWebhookEvent.id).where(
                    ProcessedWebhookEvent.provider == provider,
                    ProcessedWebhookEvent.event_id == event_id,
                )
            )
            is not None
        )
