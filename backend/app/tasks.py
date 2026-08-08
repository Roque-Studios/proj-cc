"""Celery tasks for the Content Creator Engine.

``debug_ping`` and ``debug_add`` are lightweight test tasks used to verify the
broker -> worker -> result-backend pipeline (e.g. the docker-compose acceptance
check). ``expire_canceled_subscriptions`` is the real scheduled task that
flips non-renewing subscriptions to canceled after their period ends, and
``notify_payment_failed`` emails a subscriber when a renewal payment fails
(entering the past-due grace period).
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

import structlog
from sqlalchemy.orm import Session

from .celery_app import celery_app
from .config import settings
from .database import SessionLocal
from .models import Subscription, User
from .services.subscriptions import SubscriptionService

logger = structlog.get_logger()


@celery_app.task(name="tasks.debug_ping", bind=True)
def debug_ping(self, message: str = "ping") -> dict:
    """Return the message back — proves the task was enqueued and executed."""
    logger.info("debug_ping executed", task_id=self.request.id, message=message)
    return {"status": "ok", "message": message, "task_id": self.request.id}


@celery_app.task(name="tasks.debug_add")
def debug_add(x: int, y: int) -> int:
    """Sum two integers — proves arguments round-trip through the broker."""
    result = x + y
    logger.info("debug_add executed", x=x, y=y, result=result)
    return result


@celery_app.task(name="tasks.expire_canceled_subscriptions")
def expire_canceled_subscriptions() -> int:
    """Scheduled (beat): expire non-renewing subscriptions past their period end.

    Runs in the worker process, which opens its own DB session (no request
    context available). Returns the number of subscriptions expired.
    """
    db: Session = SessionLocal()
    try:
        service = SubscriptionService(db)
        count = service.expire_canceled_subscriptions()
        logger.info("expired canceled subscriptions", count=count)
        return count
    finally:
        db.close()


@celery_app.task(name="tasks.notify_payment_failed")
def notify_payment_failed(subscription_id: int) -> bool:
    """Email the subscriber that a renewal payment failed (grace period).

    Enqueued by ``SubscriptionService`` when a subscription transitions into
    ``past_due``. Runs in the worker with its own DB session. When SMTP is not
    configured the notification degrades to a structured warning log (dev /
    mock setups) so the task is always safe to run.
    """
    db: Session = SessionLocal()
    try:
        subscription = db.get(Subscription, subscription_id)
        if subscription is None:
            logger.warning(
                "payment failed notification: subscription not found",
                subscription_id=subscription_id,
            )
            return False
        subscriber = db.get(User, subscription.subscriber_id)
        creator = db.get(User, subscription.creator_id)
        if subscriber is None or creator is None:
            logger.warning(
                "payment failed notification: user missing",
                subscription_id=subscription_id,
            )
            return False

        creator_name = creator.username
        if (
            creator.creator_profile is not None
            and creator.creator_profile.display_name
        ):
            creator_name = creator.creator_profile.display_name
        period_end = subscription.current_period_end
        period_end_hint = (
            period_end.strftime("%B %d, %Y") if period_end else "the end of your billing period"
        )

        subject = "Your subscription payment failed"
        body = (
            f"Hi {subscriber.username},\n\n"
            f"We couldn't charge your payment method for {creator_name}. "
            f"Your subscription is now in the past-due grace period — access "
            f"is on hold until you update your payment method (due by "
            f"{period_end_hint}).\n\n"
            f"To keep your access, please update your payment method before "
            f"{period_end_hint}.\n"
        )

        if settings.SMTP_HOST:
            sent = _send_email(subscriber.email, subject, body)
            logger.info(
                "payment failed notification handled",
                subscription_id=subscription_id,
                recipient=subscriber.email,
                sent=sent,
            )
            return sent
        logger.warning(
            "payment failed notification skipped (SMTP not configured)",
            subscription_id=subscription_id,
            recipient=subscriber.email,
            subject=subject,
        )
        return False
    finally:
        db.close()


@celery_app.task(name="tasks.notify_password_reset")
def notify_password_reset(email: str, reset_token: str) -> bool:
    """Email a user their password-reset code.

    Enqueued by ``POST /auth/forgot-password`` when SMTP is configured; the
    request itself never waits on mail. When SMTP is not configured the task
    degrades to a structured log (the endpoint then hands the code back as
    ``dev_token`` so the flow still works in dev).
    """
    subject = "Your password reset code"
    body = (
        "Hi,\n\n"
        "Someone requested a password reset for your account. "
        "Your reset code is:\n\n"
        f"{reset_token}\n\n"
        "Enter it on the sign-in page together with a new password. "
        "The code expires in "
        f"{settings.RESET_TOKEN_EXPIRE_MINUTES} minutes.\n\n"
        "If you didn't request this, you can safely ignore this email — "
        "your password stays unchanged.\n"
    )
    if settings.SMTP_HOST:
        sent = _send_email(email, subject, body)
        logger.info(
            "password reset notification handled",
            recipient=email,
            sent=sent,
        )
        return sent
    logger.warning(
        "password reset email skipped (SMTP not configured)",
        recipient=email,
        subject=subject,
    )
    return False


def _send_email(to_email: str, subject: str, body: str) -> bool:
    """Send a plain-text email via SMTP; never raises (task must not crash)."""
    message = EmailMessage()
    message["From"] = settings.SMTP_FROM or "noreply@localhost"
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            if settings.SMTP_TLS:
                server.starttls()
            if settings.SMTP_USERNAME:
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.send_message(message)
        return True
    except Exception as exc:  # noqa: BLE001 — mail must never crash the task
        logger.error(
            "payment failed notification send error",
            to=to_email,
            error=str(exc),
        )
        return False
