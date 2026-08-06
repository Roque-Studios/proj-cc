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
from ..models import PaidUnlock, Post, ProcessedWebhookEvent
from ..payments import ChargeRequest, WebhookEvent, get_payment_provider

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

        A refunded row (``refunded_at`` set) is excluded: the broadcast is
        locked again until the subscriber re-purchases.
        """
        return self.db.scalar(
            select(PaidUnlock).where(
                PaidUnlock.subscriber_id == subscriber_id,
                PaidUnlock.post_id == post_id,
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
                PaidUnlock.refunded_at.is_(None),
            )
        ).all()
        return set(rows)

    # ------------------------------------------------------------------ #
    # Unlock flow
    # ------------------------------------------------------------------ #

    def unlock(self, subscriber_id: int, post: Post) -> tuple[PaidUnlock, bool]:
        """Charge the one-time price and record the unlock.

        Returns ``(unlock, created)``: an active existing unlock is returned
        unchanged with ``created=False`` — a repeat never charges again. A
        **refunded** row is reactivated in place (a fresh charge replaces the
        external ref, ``refunded_at`` cleared) with ``created=True``, so the
        unique (subscriber, post) pair still holds exactly one row. Raises
        :class:`BroadcastNotPaidError` for a regular post and
        :class:`PaymentFailedError` when the charge fails (no unlock is granted
        on a failed payment).

        Note: the provider is charged **before** the row commits, so two
        perfectly concurrent first-time unlocks for the same (subscriber, post)
        could both pass the check and both charge, with the unique constraint
        resolving the row race afterwards (the loser returns the winner's row).
        Concurrent **re-purchases** of a refunded row have the same exposure
        (both charge, then both update the same row — the row keeps whichever
        ref committed last). The charge metadata carries
        ``subscriber_id``/``post_id``, so a reconciliation sweep could refund
        the loser — acceptable for a solo platform.
        """
        row = self.db.scalar(
            select(PaidUnlock).where(
                PaidUnlock.subscriber_id == subscriber_id,
                PaidUnlock.post_id == post.id,
            )
        )
        if row is not None and row.refunded_at is None:
            return row, False

        if post.broadcast_price_cents is None:
            raise BroadcastNotPaidError("This post is not a paid broadcast")

        result = self.provider.charge_one_time(
            ChargeRequest(
                amount_cents=post.broadcast_price_cents,
                currency="usd",
                description=f"Unlock broadcast post {post.id}",
                metadata={
                    "subscriber_id": str(subscriber_id),
                    "post_id": str(post.id),
                },
            )
        )
        if result.status != "succeeded":
            raise PaymentFailedError(f"One-time payment failed: {result.status}")

        if row is not None:
            # Re-purchase after a refund: reactivate the same row in place.
            row.payment_provider = self.provider.name
            row.external_ref = result.external_ref
            row.refunded_at = None
            unlock = row
        else:
            unlock = PaidUnlock(
                subscriber_id=subscriber_id,
                post_id=post.id,
                payment_provider=self.provider.name,
                external_ref=result.external_ref,
            )
            self.db.add(unlock)
        try:
            self.db.commit()
        except IntegrityError:
            # A concurrent first-time unlock for the same (subscriber, post)
            # won the row race (unique constraint) — return their row. The
            # losing request's gateway charge was already made; the charge
            # metadata carries the (subscriber, post) pair so a reconciliation
            # sweep can match and refund it if ever needed.
            self.db.rollback()
            existing = self.get_unlock(subscriber_id, post.id)
            if existing is not None:
                return existing, False
            raise
        self.db.refresh(unlock)
        logger.info(
            "paid_unlock_created",
            subscriber_id=subscriber_id,
            post_id=post.id,
            amount_cents=post.broadcast_price_cents,
            external_ref=result.external_ref,
            reactivated=(row is not None),
        )
        return unlock, True

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
