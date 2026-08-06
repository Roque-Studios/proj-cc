"""Unit tests for the payment gateway abstraction layer.

The acceptance criteria: unit tests mock the PaymentProvider interface so the
core subscription flows can be exercised independent of any real gateway — and
switching/adding a gateway requires no changes to the business logic.

These tests use the in-memory ``MockPaymentProvider`` (a faithful stand-in for a
real gateway) and a hand-rolled ``FakePaymentProvider`` recording calls, proving
``SubscriptionService`` behaves identically no matter which implementation is
injected.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.models import Subscription, SubscriptionStatus, User, UserRole
from app.payments import (
    ChargeRequest,
    PaymentProvider,
    ProviderConfigurationError,
    SubscriptionIntent,
    SubscriptionResult,
    WebhookEvent,
    WebhookEventType,
    WebhookVerificationError,
    get_payment_provider,
)
from app.payments.mock import MockPaymentProvider
from app.services.subscriptions import SubscriptionService


def _create_user(db, email: str, role=UserRole.registered) -> User:
    user = User(
        email=email,
        username=email.split("@")[0],
        hashed_password="not-used-in-tests",
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _subscriber_and_creator(db):
    subscriber = _create_user(db, "sub@example.com")
    creator = _create_user(db, "creator@example.com", role=UserRole.creator)
    return subscriber, creator


# --------------------------------------------------------------------------- #
# Mock provider unit tests (the interface contract)
# --------------------------------------------------------------------------- #

def test_mock_provider_creates_and_cancels_subscription():
    provider = MockPaymentProvider()
    result = provider.create_subscription(
        SubscriptionIntent(plan_id="plan_x", metadata={"subscriber_id": "1"})
    )
    assert result.external_ref.startswith("sub_mock_")
    assert result.status == "active"
    assert result.checkout_url.startswith("https://mock.checkout/")
    assert provider.subscriptions[result.external_ref]["status"] == "active"

    provider.cancel_subscription(result.external_ref)
    assert provider.subscriptions[result.external_ref]["status"] == "canceled"


def test_mock_provider_verifies_signed_webhook_and_rejects_forged():
    provider = MockPaymentProvider()
    body = MockPaymentProvider.make_webhook_body(
        "subscription.canceled", external_ref="sub_mock_1", subscription_status="canceled"
    )
    headers = MockPaymentProvider.sign_body(body)

    event = provider.verify_webhook(body, headers)
    assert event.event_type == WebhookEventType.subscription_canceled
    assert event.external_ref == "sub_mock_1"
    assert event.subscription_status == "canceled"

    # Forged signature must be rejected.
    forged = dict(headers, **{"X-Mock-Signature": "deadbeef"})
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook(body, forged)


def test_mock_provider_charges_one_time():
    provider = MockPaymentProvider()
    result = provider.charge_one_time(ChargeRequest(amount_cents=500, currency="usd"))
    assert result.external_ref.startswith("ch_mock_")
    assert result.status == "succeeded"
    assert result.amount_cents == 500


# --------------------------------------------------------------------------- #
# A fake provider recording calls — proves business logic only uses the interface
# --------------------------------------------------------------------------- #

class FakePaymentProvider(PaymentProvider):
    """Records every interface call; canned responses."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.canceled: list[str] = []
        self.customer_ref = "fake_cus_1"

    def create_customer(
        self, email: str, name: str | None = None, metadata: dict | None = None
    ) -> str:
        self.calls.append(("create_customer", (email,), metadata or {}))
        return self.customer_ref

    def create_subscription(self, intent: SubscriptionIntent) -> SubscriptionResult:
        self.calls.append(("create_subscription", (), intent.__dict__))
        return SubscriptionResult(
            external_ref="fake_sub_1",
            status="active",
            checkout_url="https://fake.checkout/1",
            current_period_start=datetime.now(timezone.utc),
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
        )

    def cancel_subscription(self, external_ref: str) -> None:
        self.calls.append(("cancel_subscription", (external_ref,), {}))
        self.canceled.append(external_ref)

    def cancel_at_period_end(self, external_ref: str) -> None:
        self.calls.append(("cancel_at_period_end", (external_ref,), {}))

    def verify_webhook(self, body: bytes, headers) -> WebhookEvent:
        self.calls.append(("verify_webhook", (body,), dict(headers)))
        return WebhookEvent(
            provider=self.name,
            event_type=WebhookEventType.subscription_canceled,
            external_ref="fake_sub_1",
            subscription_status="canceled",
        )

    def charge_one_time(self, request) -> ChargeResult:
        self.calls.append(("charge_one_time", (), request.__dict__))
        return ChargeResult(
            external_ref="fake_ch_1",
            status="succeeded",
            amount_cents=request.amount_cents,
            currency=request.currency,
        )


# --------------------------------------------------------------------------- #
# Core flows with the mock provider
# --------------------------------------------------------------------------- #

def test_service_creates_subscription_via_mock(db_session):
    """A fresh subscription is pending (incomplete) with a hosted checkout url."""
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        service = SubscriptionService(db, provider=MockPaymentProvider())
        subscription = service.create_subscription(
            subscriber.id, creator.id, plan_id="plan_x"
        )
        db.refresh(subscription)

        assert subscription.external_ref.startswith("sub_mock_")
        assert subscription.payment_provider == "mock"
        assert subscription.status == SubscriptionStatus.incomplete
        assert subscription.checkout_url.startswith("https://mock.checkout/")
        assert subscription.subscriber_id == subscriber.id
        assert subscription.creator_id == creator.id
        assert subscription.current_period_end is not None


def test_service_cancel_subscription_updates_status(db_session):
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        service = SubscriptionService(db, provider=MockPaymentProvider())
        subscription = service.create_subscription(subscriber.id, creator.id, "plan_x")
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.incomplete

        service.cancel_subscription(subscription)
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.canceled


def test_service_webhook_canceled_reconciles_status(db_session):
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        service = SubscriptionService(db, provider=MockPaymentProvider())
        subscription = service.create_subscription(subscriber.id, creator.id, "plan_x")
        db.refresh(subscription)

        body = MockPaymentProvider.make_webhook_body(
            "subscription.canceled",
            external_ref=subscription.external_ref,
            subscription_status="canceled",
        )
        service.handle_webhook(body, MockPaymentProvider.sign_body(body))
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.canceled


def test_service_webhook_rejects_forged_signature(db_session):
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        service = SubscriptionService(db, provider=MockPaymentProvider())
        body = MockPaymentProvider.make_webhook_body(
            "payment.failed", external_ref="sub_mock_1"
        )
        with pytest.raises(WebhookVerificationError):
            service.handle_webhook(
                body, {"X-Mock-Signature": "forged-signature"}
            )


def test_service_payment_failed_leaves_incomplete_when_pending(db_session):
    """Acceptance: a failed payment on a pending subscription stays incomplete."""
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        service = SubscriptionService(db, provider=MockPaymentProvider())
        subscription = service.create_subscription(subscriber.id, creator.id, "plan_x")
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.incomplete

        body = MockPaymentProvider.make_webhook_body(
            "payment.failed", external_ref=subscription.external_ref
        )
        service.handle_webhook(body, MockPaymentProvider.sign_body(body))
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.incomplete


def test_service_payment_failed_marks_active_as_past_due(db_session):
    """A failed renewal on an *active* subscription moves it to past_due."""
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        service = SubscriptionService(db, provider=MockPaymentProvider())
        subscription = service.create_subscription(subscriber.id, creator.id, "plan_x")
        db.refresh(subscription)

        # Activate first (payment succeeded).
        paid = MockPaymentProvider.make_webhook_body(
            "payment.succeeded", external_ref=subscription.external_ref
        )
        service.handle_webhook(paid, MockPaymentProvider.sign_body(paid))
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active

        failed = MockPaymentProvider.make_webhook_body(
            "payment.failed", external_ref=subscription.external_ref
        )
        service.handle_webhook(failed, MockPaymentProvider.sign_body(failed))
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.past_due


def test_service_payment_succeeded_activates_subscription(db_session):
    """Acceptance: a successful payment activates the subscription and clears checkout."""
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        service = SubscriptionService(db, provider=MockPaymentProvider())
        subscription = service.create_subscription(subscriber.id, creator.id, "plan_x")
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.incomplete
        assert subscription.checkout_url is not None

        body = MockPaymentProvider.make_webhook_body(
            "payment.succeeded", external_ref=subscription.external_ref
        )
        service.handle_webhook(body, MockPaymentProvider.sign_body(body))
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        # Checkout url is cleared once active — no longer pending.
        assert subscription.checkout_url is None


def test_service_charge_one_time_returns_result(db_session):
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        service = SubscriptionService(db, provider=MockPaymentProvider())
        result = service.charge_one_time(subscriber.id, creator.id, 500, "usd")
        assert result.external_ref.startswith("ch_mock_")
        assert result.status == "succeeded"


# --------------------------------------------------------------------------- #
# Gateway independence: same flows, different provider — no logic changes
# --------------------------------------------------------------------------- #

def test_business_logic_identical_across_providers(db_session):
    """The exact same service calls work with the fake provider."""

    def _run_flow(provider: PaymentProvider, suffix: str):
        with db_session as db:
            subscriber = _create_user(db, f"sub-{suffix}@example.com")
            creator = _create_user(db, f"creator-{suffix}@example.com", role=UserRole.creator)
            service = SubscriptionService(db, provider=provider)
            subscription = service.create_subscription(subscriber.id, creator.id, "plan_x")
            db.refresh(subscription)
            service.cancel_subscription(subscription)
            db.refresh(subscription)
            return subscription.payment_provider, subscription.status

    mock_result = _run_flow(MockPaymentProvider(), "mock")
    fake_result = _run_flow(FakePaymentProvider(), "fake")
    assert mock_result == ("mock", SubscriptionStatus.canceled)
    assert fake_result == ("fake", SubscriptionStatus.canceled)


def test_fake_provider_calls_recorded(db_session):
    """Assert the service only invokes interface methods, with sane arguments."""
    fake = FakePaymentProvider()
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        subscriber_id = subscriber.id
        creator_id = creator.id
        service = SubscriptionService(db, provider=fake)
        subscription = service.create_subscription(subscriber_id, creator_id, "plan_x")
        service.cancel_subscription(subscription)

    assert [name for name, _, _ in fake.calls] == [
        "create_customer",
        "create_subscription",
        "cancel_subscription",
    ]
    create_call = fake.calls[1]
    assert create_call[2]["metadata"] == {
        "subscriber_id": str(subscriber_id),
        "creator_id": str(creator_id),
    }
    assert create_call[2]["customer_ref"] == "fake_cus_1"
    assert fake.canceled == ["fake_sub_1"]


def test_service_creates_customer_only_once(db_session):
    """The gateway customer is created lazily and cached on the user."""
    fake = FakePaymentProvider()
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        subscriber_id = subscriber.id
        creator_id = creator.id
        service = SubscriptionService(db, provider=fake)
        service.create_subscription(subscriber_id, creator_id, "plan_x")
        service.create_subscription(subscriber_id, creator_id, "plan_y")
        db.refresh(subscriber)
        assert subscriber.payment_customer_id == "fake_cus_1"

    customer_calls = [c for c in fake.calls if c[0] == "create_customer"]
    assert len(customer_calls) == 1


# --------------------------------------------------------------------------- #
# Factory / switching gateways
# --------------------------------------------------------------------------- #

def test_factory_returns_mock_by_default(monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_PROVIDER", "mock")
    provider = get_payment_provider(settings)
    assert isinstance(provider, MockPaymentProvider)


def test_factory_switches_to_stripe_with_credentials(monkeypatch):
    from app.payments.stripe import StripePaymentProvider

    monkeypatch.setattr(settings, "PAYMENT_PROVIDER", "stripe")
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_123")
    provider = get_payment_provider(settings)
    assert isinstance(provider, StripePaymentProvider)


def test_factory_fails_fast_missing_stripe_credentials(monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_PROVIDER", "stripe")
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "")
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_123")
    with pytest.raises(ProviderConfigurationError):
        get_payment_provider(settings)


def test_factory_switches_to_paypal_with_credentials(monkeypatch):
    from app.payments.paypal import PayPalPaymentProvider

    monkeypatch.setattr(settings, "PAYMENT_PROVIDER", "paypal")
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_ID", "client_id")
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_SECRET", "client_secret")
    monkeypatch.setattr(settings, "PAYPAL_WEBHOOK_ID", "wh_id")
    provider = get_payment_provider(settings)
    assert isinstance(provider, PayPalPaymentProvider)


def test_factory_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_PROVIDER", "bitcoin-cash")
    with pytest.raises(ProviderConfigurationError):
        get_payment_provider(settings)


# --------------------------------------------------------------------------- #
# Stripe webhook signature math (deterministic, no network)
# --------------------------------------------------------------------------- #

def test_stripe_webhook_signature_verification():
    import hashlib
    import hmac
    import json
    import time

    from app.payments.stripe import StripePaymentProvider

    webhook_secret = "whsec_test"
    provider = StripePaymentProvider("sk_test_123", webhook_secret)

    body = json.dumps(
        {
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_123", "status": "canceled"}},
        }
    ).encode()
    timestamp = int(time.time())
    signed = f"{timestamp}.".encode() + body
    sig = hmac.new(webhook_secret.encode(), signed, hashlib.sha256).hexdigest()
    headers = {"stripe-signature": f"t={timestamp},v1={sig}"}

    event = provider.verify_webhook(body, headers)
    assert event.event_type == WebhookEventType.subscription_canceled
    assert event.external_ref == "sub_123"

    # Tampered body must fail verification.
    bad_headers = {"stripe-signature": f"t={timestamp},v1={'0' * 64}"}
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook(body, bad_headers)

    # Malformed header and replayed (old) timestamp must fail cleanly.
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook(body, {"stripe-signature": "v1=abc"})
    old_headers = {"stripe-signature": f"t=1,v1={sig}"}
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook(body, old_headers)


def test_service_create_subscription_is_idempotent(db_session):
    """A second subscribe to the same creator returns the existing row (unique constraint)."""
    provider = MockPaymentProvider()
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        service = SubscriptionService(db, provider=provider)
        first = service.create_subscription(subscriber.id, creator.id, "plan_x")
        second = service.create_subscription(subscriber.id, creator.id, "plan_x")
        assert second.id == first.id
        assert db.query(Subscription).count() == 1
