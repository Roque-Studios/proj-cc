"""Stripe Billing integration tests (test-mode, no network).

The Stripe REST API is simulated with ``httpx.MockTransport`` so the provider's
real request-building, auth, and signature-verification code runs against a
faithful fake of Stripe's responses. Covers the acceptance criteria:

- test-mode subscription flow creates an incomplete (pending payment) Subscription
  (customer created, checkout session opened, row persisted with the external ref);
- ``invoice.paid`` webhook reconciles the subscription to ``active``;
- ``invoice.payment_failed`` webhook reconciles it to ``past_due``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import httpx
import pytest

from app.models import Subscription, SubscriptionStatus, User, UserRole
from app.payments.stripe import StripePaymentProvider
from app.payments import WebhookVerificationError
from app.services.subscriptions import SubscriptionService

WEBHOOK_SECRET = "whsec_integration_test"


# --------------------------------------------------------------------------- #
# A fake Stripe API (httpx.MockTransport handler)
# --------------------------------------------------------------------------- #

class FakeStripeAPI:
    """In-memory Stripe: customers, checkout sessions, subscriptions."""

    def __init__(self) -> None:
        self.customers: dict[str, dict] = {}
        self.checkout_sessions: dict[str, dict] = {}
        self.subscriptions: dict[str, dict] = {}
        self._seq = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/customers":
            return self._create_customer(request)
        if request.method == "POST" and path == "/v1/checkout/sessions":
            return self._create_checkout_session(request)
        if request.method == "POST" and path.startswith("/v1/checkout/sessions/"):
            return self._expire_checkout_session(request)
        if request.method == "POST" and path.startswith("/v1/payment_intents"):
            return self._create_payment_intent(request)
        return httpx.Response(404, text='{"error": "not found"}')

    def _expire_checkout_session(self, request: httpx.Request) -> httpx.Response:
        sid = request.url.path.split("/")[-2]
        if sid not in self.checkout_sessions:
            return httpx.Response(404, text='{"error": "no such checkout session"}')
        return httpx.Response(200, json={"id": sid, "status": "expired"})

    def _create_customer(self, request: httpx.Request) -> httpx.Response:
        self._seq += 1
        cid = f"cus_{self._seq}"
        self.customers[cid] = {"id": cid, "email": ""}
        return httpx.Response(200, json={"id": cid, "email": ""})

    def _create_checkout_session(self, request: httpx.Request) -> httpx.Response:
        self._seq += 1
        sid = f"cs_test_{self._seq}"
        self.checkout_sessions[sid] = {"id": sid}
        return httpx.Response(
            200,
            json={
                "id": sid,
                "url": f"https://checkout.stripe.com/pay/{sid}",
                "status": "open",
            },
        )

    def _create_payment_intent(self, request: httpx.Request) -> httpx.Response:
        self._seq += 1
        return httpx.Response(200, json={"id": f"pi_{self._seq}", "status": "succeeded"})


def _stripe_provider(fake_api: FakeStripeAPI) -> StripePaymentProvider:
    return StripePaymentProvider(
        secret_key="sk_test_integration",
        webhook_secret=WEBHOOK_SECRET,
        transport=httpx.MockTransport(fake_api.handle),
    )


def _signed_webhook(provider: StripePaymentProvider, payload: dict) -> tuple[bytes, dict]:
    """Sign a webhook body the way Stripe does (t=...,v1=...)."""
    body = json.dumps(payload).encode()
    timestamp = int(time.time())
    signed = f"{timestamp}.".encode() + body
    sig = hmac.new(
        provider.webhook_secret.encode(), signed, hashlib.sha256
    ).hexdigest()
    return body, {"stripe-signature": f"t={timestamp},v1={sig}"}


def _create_users(db):
    subscriber = User(
        email="stripe-sub@example.com",
        username="stripe-sub",
        hashed_password="not-used-in-tests",
        role=UserRole.registered,
        is_active=True,
    )
    creator = User(
        email="stripe-creator@example.com",
        username="stripe-creator",
        hashed_password="not-used-in-tests",
        role=UserRole.creator,
        is_active=True,
    )
    db.add_all([subscriber, creator])
    db.commit()
    db.refresh(subscriber)
    db.refresh(creator)
    return subscriber, creator


# --------------------------------------------------------------------------- #
# Test-mode subscription flow
# --------------------------------------------------------------------------- #

def test_stripe_flow_creates_incomplete_pending_subscription(db_session):
    """Customer is created, checkout opened, local row persisted as incomplete."""
    fake_api = FakeStripeAPI()
    provider = _stripe_provider(fake_api)

    with db_session as db:
        subscriber, creator = _create_users(db)
        subscriber_id = subscriber.id
        creator_id = creator.id
        service = SubscriptionService(db, provider=provider)
        subscription = service.create_subscription(
            subscriber_id,
            creator_id,
            plan_id="price_monthly_1",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )
        db.refresh(subscription)
        db.refresh(subscriber)

        assert subscription.payment_provider == "stripe"
        assert subscription.status == SubscriptionStatus.incomplete
        assert subscription.checkout_url.startswith("https://checkout.stripe.com/")
        assert subscription.external_ref.startswith("cs_test_")
        # Checkout doesn't return the billing period; it arrives via webhooks.
        assert subscription.current_period_end is None
        # Customer was created at Stripe and cached on the user.
        assert subscriber.payment_customer_id.startswith("cus_")
        assert list(fake_api.customers) == [subscriber.payment_customer_id]
        assert len(fake_api.checkout_sessions) == 1


def test_stripe_checkout_session_uses_existing_customer(db_session):
    """Subscribing to a second creator reuses the cached customer (no dup creation)."""
    fake_api = FakeStripeAPI()
    provider = _stripe_provider(fake_api)
    captured: list[dict] = []

    original_post = provider._client.post

    def spy_post(url, **kwargs):
        captured.append(kwargs.get("data", {}))
        return original_post(url, **kwargs)

    provider._client.post = spy_post  # type: ignore[method-assign]

    with db_session as db:
        subscriber, creator_a = _create_users(db)
        creator_b = User(
            email="stripe-creator-b@example.com",
            username="stripe-creator-b",
            hashed_password="not-used-in-tests",
            role=UserRole.creator,
            is_active=True,
        )
        db.add(creator_b)
        db.commit()
        db.refresh(creator_b)
        subscriber_id = subscriber.id
        service = SubscriptionService(db, provider=provider)
        service.create_subscription(subscriber_id, creator_a.id, "price_monthly_1")
        service.create_subscription(subscriber_id, creator_b.id, "price_monthly_2")

        db.refresh(subscriber)
        assert subscriber.payment_customer_id.startswith("cus_")
        assert len(fake_api.customers) == 1  # only one customer created

    checkout_calls = [c for c in captured if c.get("mode") == "subscription"]
    assert len(checkout_calls) == 2
    assert all("customer" in c for c in checkout_calls)


# --------------------------------------------------------------------------- #
# Invoice webhook reconciliation (acceptance: both events)
# --------------------------------------------------------------------------- #

def _setup_subscription(db, fake_api, provider):
    """Create users + subscription, then simulate checkout completion and
    return the reconciled local Subscription (external_ref now the sub id)."""
    subscriber, creator = _create_users(db)
    subscriber_id = subscriber.id
    creator_id = creator.id
    service = SubscriptionService(db, provider=provider)
    subscription = service.create_subscription(subscriber_id, creator_id, "price_monthly_1")
    db.refresh(subscription)
    checkout_ref = subscription.external_ref

    # Stripe fires checkout.session.completed once the customer pays.
    body, headers = _signed_webhook(
        provider,
        {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": checkout_ref,
                    "subscription": "sub_test_1",
                    "status": "complete",
                    "metadata": {
                        "subscriber_id": str(subscriber_id),
                        "creator_id": str(creator_id),
                    },
                }
            },
        },
    )
    service.handle_webhook(body, headers)
    db.refresh(subscription)
    assert subscription.external_ref == "sub_test_1"
    return subscription


def test_stripe_invoice_paid_webhook_sets_active(db_session):
    fake_api = FakeStripeAPI()
    provider = _stripe_provider(fake_api)

    with db_session as db:
        subscription = _setup_subscription(db, fake_api, provider)
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active

        # invoice.paid -> status stays active (first renewal succeeds) and the
        # billing period is recorded.
        period_start = int(time.time()) - 86400
        period_end = int(time.time()) + 30 * 86400
        body, headers = _signed_webhook(
            provider,
            {
                "type": "invoice.paid",
                "data": {
                    "object": {
                        "id": "in_1",
                        "subscription": "sub_test_1",
                        "status": "paid",
                        "period_start": period_start,
                        "period_end": period_end,
                    }
                },
            },
        )
        service = SubscriptionService(db, provider=provider)
        event = service.handle_webhook(body, headers)
        db.refresh(subscription)
        assert event.event_type.value == "payment.succeeded"
        assert subscription.status == SubscriptionStatus.active
        # SQLite stores naive UTC datetimes; compare by epoch seconds.
        assert int(subscription.current_period_start.timestamp()) == period_start
        assert int(subscription.current_period_end.timestamp()) == period_end
        assert subscription.checkout_url is None


def test_stripe_invoice_payment_failed_keeps_incomplete_before_paid(db_session):
    """Acceptance: a failed payment before any success leaves the subscription incomplete."""
    fake_api = FakeStripeAPI()
    provider = _stripe_provider(fake_api)

    with db_session as db:
        subscriber, creator = _create_users(db)
        subscriber_id = subscriber.id
        creator_id = creator.id
        service = SubscriptionService(db, provider=provider)
        subscription = service.create_subscription(subscriber_id, creator_id, "price_monthly_1")
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.incomplete
        checkout_ref = subscription.external_ref

        # invoice.payment_failed arrives before the payment ever succeeded.
        body, headers = _signed_webhook(
            provider,
            {
                "type": "invoice.payment_failed",
                "data": {
                    "object": {
                        "id": "in_0",
                        "subscription": "sub_test_0",
                        "status": "open",
                    }
                },
            },
        )
        service.handle_webhook(body, headers)
        db.refresh(subscription)
        # Acceptance: failed payment leaves status as 'incomplete'.
        assert subscription.status == SubscriptionStatus.incomplete
        # Still pending: the checkout url remains available for retry.
        assert subscription.checkout_url.startswith("https://checkout.stripe.com/")
        assert subscription.external_ref == checkout_ref


def test_stripe_invoice_payment_failed_webhook_sets_past_due(db_session):
    fake_api = FakeStripeAPI()
    provider = _stripe_provider(fake_api)

    with db_session as db:
        subscription = _setup_subscription(db, fake_api, provider)
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active

        # invoice.payment_failed -> past_due (renewal failed).
        body, headers = _signed_webhook(
            provider,
            {
                "type": "invoice.payment_failed",
                "data": {
                    "object": {
                        "id": "in_2",
                        "subscription": "sub_test_1",
                        "status": "open",
                    }
                },
            },
        )
        service = SubscriptionService(db, provider=provider)
        event = service.handle_webhook(body, headers)
        db.refresh(subscription)
        assert event.event_type.value == "payment.failed"
        assert subscription.status == SubscriptionStatus.past_due


def test_stripe_resubscribe_reactivates_canceled_row(db_session):
    """Re-subscribing after cancel reactivates the same (subscriber, creator) row."""
    fake_api = FakeStripeAPI()
    provider = _stripe_provider(fake_api)

    with db_session as db:
        subscriber, creator = _create_users(db)
        subscriber_id = subscriber.id
        creator_id = creator.id
        service = SubscriptionService(db, provider=provider)
        subscription = service.create_subscription(subscriber_id, creator_id, "price_monthly_1")
        db.refresh(subscription)
        first_ref = subscription.external_ref

        service.cancel_subscription(subscription)
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.canceled

        # Re-subscribe: same row is reactivated (pending payment again).
        renewed = service.create_subscription(subscriber_id, creator_id, "price_monthly_1")
        db.refresh(renewed)
        assert renewed.id == subscription.id
        assert renewed.status == SubscriptionStatus.incomplete
        assert renewed.external_ref != first_ref
        assert db.query(Subscription).count() == 1


def test_stripe_webhook_forged_signature_rejected(db_session):
    fake_api = FakeStripeAPI()
    provider = _stripe_provider(fake_api)

    body = json.dumps(
        {"type": "invoice.paid", "data": {"object": {"subscription": "sub_x"}}}
    ).encode()
    # Fresh timestamp with a bad v1= exercises the HMAC-mismatch branch
    # (an old timestamp would instead trip the replay-tolerance check).
    fresh = int(time.time())
    headers = {"stripe-signature": f"t={fresh},v1={'0' * 64}"}
    with db_session as db:
        service = SubscriptionService(db, provider=provider)
        with pytest.raises(WebhookVerificationError):
            service.handle_webhook(body, headers)
