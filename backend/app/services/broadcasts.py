"""Broadcast business logic: one-time paid unlocks for creator broadcasts.

A **paid broadcast** is a post with ``broadcast_price_cents`` set: it reaches
all subscribers as a locked preview, and each subscriber pays a one-time price
to unlock full media access. ``BroadcastService`` owns the unlock lifecycle —
the one-time charge through the ``PaymentProvider`` abstraction plus the
``BroadcastUnlock`` record — and the lock/unlock queries used by the feed and
the media endpoint.

The provider is injected (constructor), defaulting to the configured one from
``get_payment_provider``; tests inject the mock/fake provider directly (same
pattern as ``SubscriptionService``).
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..models import BroadcastUnlock, Post
from ..payments import ChargeRequest, get_payment_provider

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

    def get_unlock(self, subscriber_id: int, post_id: int) -> BroadcastUnlock | None:
        """The subscriber's unlock row for a post, or None while locked."""
        return self.db.scalar(
            select(BroadcastUnlock).where(
                BroadcastUnlock.subscriber_id == subscriber_id,
                BroadcastUnlock.post_id == post_id,
            )
        )

    def is_unlocked(self, subscriber_id: int, post_id: int) -> bool:
        """True if the subscriber already paid to unlock this broadcast."""
        return self.get_unlock(subscriber_id, post_id) is not None

    def unlocked_post_ids(self, subscriber_id: int, post_ids: list[int]) -> set[int]:
        """The subset of ``post_ids`` the subscriber has unlocked (one query)."""
        if not post_ids:
            return set()
        rows = self.db.scalars(
            select(BroadcastUnlock.post_id).where(
                BroadcastUnlock.subscriber_id == subscriber_id,
                BroadcastUnlock.post_id.in_(post_ids),
            )
        ).all()
        return set(rows)

    # ------------------------------------------------------------------ #
    # Unlock flow
    # ------------------------------------------------------------------ #

    def unlock(self, subscriber_id: int, post: Post) -> tuple[BroadcastUnlock, bool]:
        """Charge the one-time price and record the unlock.

        Returns ``(unlock, created)``: an existing unlock (the subscriber
        already paid) is returned unchanged with ``created=False`` — a repeat
        never charges again. Raises :class:`BroadcastNotPaidError` for a
        regular post and :class:`PaymentFailedError` when the charge fails (no
        unlock row is written on a failed payment).

        Note: the provider is charged **before** the row commits, so two
        perfectly concurrent unlocks for the same (subscriber, post) could both
        pass the check and both charge, with the unique constraint resolving the
        row race afterwards (the loser returns the winner's row). The charge
        metadata carries ``subscriber_id``/``post_id``, so a reconciliation
        sweep could refund the loser — acceptable for a solo platform.
        """
        existing = self.get_unlock(subscriber_id, post.id)
        if existing is not None:
            return existing, False

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

        unlock = BroadcastUnlock(
            subscriber_id=subscriber_id,
            post_id=post.id,
            payment_provider=self.provider.name,
            external_ref=result.external_ref,
        )
        self.db.add(unlock)
        try:
            self.db.commit()
        except IntegrityError:
            # A concurrent unlock for the same (subscriber, post) won the row
            # race (unique constraint) — return their row. The losing request's
            # gateway charge was already made; the charge metadata carries the
            # (subscriber, post) pair so a reconciliation sweep can match and
            # refund it if ever needed.
            self.db.rollback()
            existing = self.get_unlock(subscriber_id, post.id)
            if existing is not None:
                return existing, False
            raise
        self.db.refresh(unlock)
        logger.info(
            "broadcast_unlocked",
            subscriber_id=subscriber_id,
            post_id=post.id,
            amount_cents=post.broadcast_price_cents,
            external_ref=result.external_ref,
        )
        return unlock, True
