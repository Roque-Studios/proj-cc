"""Payment-failure notifications (renewal grace period).

Webhook handling must stay fast, so the subscription service only *enqueues*
the notification here (fire-and-forget) and the Celery worker sends the actual
email. When SMTP is not configured the task degrades to a structured log — the
stack runs fine in dev with zero mail credentials.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


def enqueue_payment_failed_notification(subscription_id: int) -> None:
    """Enqueue the payment-failed email task for the given subscription.

    Called by ``SubscriptionService`` only when a subscription *transitions*
    into the past-due (grace) state, so a flurry of failed-renewal webhooks
    results in exactly one notification per failure episode.
    """
    # Deferred import: tasks.py imports SubscriptionService, so importing it
    # here at module level would create an import cycle.
    from .tasks import notify_payment_failed

    logger.info(
        "Enqueuing payment failed notification",
        subscription_id=subscription_id,
    )
    notify_payment_failed.delay(subscription_id)
