"""Webhook handler — payment renewal & failure.

Acceptance: webhook signatures verified per provider; processing is idempotent
(a provider retry of the same event id is acknowledged without re-applying
changes — no duplicate renewal); a failed renewal moves the subscription to
``past_due`` and triggers exactly one notification (grace period). Covered with
mocked payloads for each gateway (mock / stripe / paypal).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import (
    ProcessedWebhookEvent,
    Subscription,
    SubscriptionStatus,
    User,
    UserRole,
)
from app.payments.mock import MockPaymentProvider
from app.services.subscriptions import SubscriptionService

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _user(db, email: str, role=UserRole.registered) -> User:
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
    subscriber = _user(db, "sub@example.com")
    creator = _user(db, "creator@example.com", role=UserRole.creator)
    return subscriber, creator


def _active_subscription(
    db, subscriber_id: int, creator_id: int, provider="mock", external_ref="sub_x"
) -> Subscription:
    sub = Subscription(
        subscriber_id=subscriber_id,
        creator_id=creator_id,
        status=SubscriptionStatus.active,
        current_period_start=NOW - timedelta(days=30),
        current_period_end=NOW + timedelta(days=1),
        payment_provider=provider,
        external_ref=external_ref,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def _ledger(db) -> list[tuple[str, str]]:
    return [
        (row.provider, row.event_id)
        for row in db.scalars(select(ProcessedWebhookEvent)).all()
    ]


# --------------------------------------------------------------------------- #
# Mock gateway: idempotent processing + failure notification
# --------------------------------------------------------------------------- #

def test_mock_payment_succeeded_idempotent_on_redelivery(db_session, monkeypatch):
    """The same invoice event delivered twice is applied once (no duplicate renewal)."""
    notifications = []
    monkeypatch.setattr(
        "app.services.subscriptions.enqueue_payment_failed_notification",
        lambda sub_id: notifications.append(sub_id),
    )
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        sub = _active_subscription(db, subscriber.id, creator.id)
        sub_id = sub.id
        service = SubscriptionService(db, provider=MockPaymentProvider())

        body = MockPaymentProvider.make_webhook_body(
            "payment.succeeded",
            external_ref=sub.external_ref,
            event_id="evt_mock_success_1",
        )
        headers = MockPaymentProvider.sign_body(body)

        first = service.handle_webhook(body, headers)
        db.refresh(sub)
        assert first.duplicate is False
        assert sub.status == SubscriptionStatus.active

        second = service.handle_webhook(body, headers)
        db.refresh(sub)
        assert second.duplicate is True
        assert sub.status == SubscriptionStatus.active
        assert notifications == []
        assert _ledger(db) == [("mock", "evt_mock_success_1")]


def test_mock_payment_failed_notifies_exactly_once(db_session, monkeypatch):
    """A failed renewal -> past_due + exactly one notification (grace period)."""
    notifications = []
    monkeypatch.setattr(
        "app.services.subscriptions.enqueue_payment_failed_notification",
        lambda sub_id: notifications.append(sub_id),
    )
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        sub = _active_subscription(db, subscriber.id, creator.id)
        sub_id = sub.id
        service = SubscriptionService(db, provider=MockPaymentProvider())

        body = MockPaymentProvider.make_webhook_body(
            "payment.failed",
            external_ref=sub.external_ref,
            event_id="evt_mock_fail_1",
        )
        headers = MockPaymentProvider.sign_body(body)

        event = service.handle_webhook(body, headers)
        db.refresh(sub)
        assert event.duplicate is False
        assert sub.status == SubscriptionStatus.past_due
        assert notifications == [sub_id]


def test_mock_failure_redelivery_does_not_notify_again(db_session, monkeypatch):
    """A retry of the same failure event is a duplicate: no second notification."""
    notifications = []
    monkeypatch.setattr(
        "app.services.subscriptions.enqueue_payment_failed_notification",
        lambda sub_id: notifications.append(sub_id),
    )
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        sub = _active_subscription(db, subscriber.id, creator.id)
        service = SubscriptionService(db, provider=MockPaymentProvider())

        body = MockPaymentProvider.make_webhook_body(
            "payment.failed",
            external_ref=sub.external_ref,
            event_id="evt_mock_fail_2",
        )
        headers = MockPaymentProvider.sign_body(body)

        service.handle_webhook(body, headers)
        db.refresh(sub)
        assert sub.status == SubscriptionStatus.past_due
        assert len(notifications) == 1

        # Redelivery of the exact same event -> no-op.
        service.handle_webhook(body, headers)
        db.refresh(sub)
        assert sub.status == SubscriptionStatus.past_due
        assert len(notifications) == 1


def test_second_failure_while_past_due_does_not_notify(db_session, monkeypatch):
    """A *new* failure event while already past_due: status stays, no re-notify."""
    notifications = []
    monkeypatch.setattr(
        "app.services.subscriptions.enqueue_payment_failed_notification",
        lambda sub_id: notifications.append(sub_id),
    )
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        sub = _active_subscription(db, subscriber.id, creator.id)
        service = SubscriptionService(db, provider=MockPaymentProvider())

        first = MockPaymentProvider.make_webhook_body(
            "payment.failed",
            external_ref=sub.external_ref,
            event_id="evt_mock_fail_a",
        )
        service.handle_webhook(first, MockPaymentProvider.sign_body(first))
        db.refresh(sub)
        assert sub.status == SubscriptionStatus.past_due
        assert len(notifications) == 1

        second = MockPaymentProvider.make_webhook_body(
            "payment.failed",
            external_ref=sub.external_ref,
            event_id="evt_mock_fail_b",  # distinct event, same episode
        )
        service.handle_webhook(second, MockPaymentProvider.sign_body(second))
        db.refresh(sub)
        assert sub.status == SubscriptionStatus.past_due
        assert len(notifications) == 1


def test_pending_failure_stays_incomplete_no_notification(db_session, monkeypatch):
    """A failed *initial* payment (incomplete) does not notify — nothing lost yet."""
    notifications = []
    monkeypatch.setattr(
        "app.services.subscriptions.enqueue_payment_failed_notification",
        lambda sub_id: notifications.append(sub_id),
    )
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        service = SubscriptionService(db, provider=MockPaymentProvider())
        sub = service.create_subscription(subscriber.id, creator.id, "plan_x")
        db.refresh(sub)
        assert sub.status == SubscriptionStatus.incomplete

        body = MockPaymentProvider.make_webhook_body(
            "payment.failed",
            external_ref=sub.external_ref,
            event_id="evt_mock_pending_fail",
        )
        service.handle_webhook(body, MockPaymentProvider.sign_body(body))
        db.refresh(sub)
        assert sub.status == SubscriptionStatus.incomplete
        assert notifications == []


# --------------------------------------------------------------------------- #
# Stripe gateway (MockTransport-free: pure signature math + ledger)
# --------------------------------------------------------------------------- #

def _stripe_provider() -> "StripePaymentProvider":
    from app.payments.stripe import StripePaymentProvider

    return StripePaymentProvider("sk_test_x", "whsec_test")


def _signed_stripe(provider, payload: dict) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    timestamp = int(time.time())
    signed = f"{timestamp}.".encode() + body
    sig = hmac.new(
        provider.webhook_secret.encode(), signed, hashlib.sha256
    ).hexdigest()
    return body, {"stripe-signature": f"t={timestamp},v1={sig}"}


def _stripe_invoice_paid() -> dict:
    return {
        "id": "evt_invoice_paid_1",
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": "in_1",
                "subscription": "sub_x",
                # Real Stripe invoices carry payment_intent; the reconcilable
                # ref must stay the subscription id (ref preference is
                # refund-events-only), so keep it in the fixture.
                "payment_intent": "pi_invoice_1",
                "status": "paid",
                "period_start": int((NOW - timedelta(days=30)).timestamp()),
                "period_end": int((NOW + timedelta(days=1)).timestamp()),
            }
        },
    }


def test_stripe_renewal_webhook_idempotent_on_retry(db_session, monkeypatch):
    """Stripe retries ``invoice.paid`` with the same evt id — applied once."""
    notifications = []
    monkeypatch.setattr(
        "app.services.subscriptions.enqueue_payment_failed_notification",
        lambda sub_id: notifications.append(sub_id),
    )
    provider = _stripe_provider()
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        sub = _active_subscription(db, subscriber.id, creator.id, provider="stripe")
        sub_id = sub.id
        service = SubscriptionService(db, provider=provider)

        body, headers = _signed_stripe(provider, _stripe_invoice_paid())
        first = service.handle_webhook(body, headers)
        db.refresh(sub)
        assert first.duplicate is False
        assert sub.status == SubscriptionStatus.active

        second = service.handle_webhook(body, headers)
        db.refresh(sub)
        assert second.duplicate is True
        assert sub.status == SubscriptionStatus.active
        assert notifications == []
        assert _ledger(db) == [("stripe", "evt_invoice_paid_1")]
        assert sub_id == sub.id


def test_stripe_payment_failed_notifies_once(db_session, monkeypatch):
    notifications = []
    monkeypatch.setattr(
        "app.services.subscriptions.enqueue_payment_failed_notification",
        lambda sub_id: notifications.append(sub_id),
    )
    provider = _stripe_provider()
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        sub = _active_subscription(db, subscriber.id, creator.id, provider="stripe")
        sub_id = sub.id
        service = SubscriptionService(db, provider=provider)

        payload = {
            "id": "evt_invoice_failed_1",
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "id": "in_2",
                    "subscription": "sub_x",
                    "status": "open",
                    "period_end": int((NOW + timedelta(days=1)).timestamp()),
                }
            },
        }
        body, headers = _signed_stripe(provider, payload)
        event = service.handle_webhook(body, headers)
        db.refresh(sub)
        assert event.duplicate is False
        assert sub.status == SubscriptionStatus.past_due
        assert notifications == [sub_id]


def test_stripe_webhook_forged_signature_rejected_before_ledger(db_session):
    """An unverified event must never be recorded as processed."""
    provider = _stripe_provider()
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        sub = _active_subscription(db, subscriber.id, creator.id, provider="stripe")
        service = SubscriptionService(db, provider=provider)

        body = json.dumps(_stripe_invoice_paid()).encode()
        bad_headers = {
            "stripe-signature": f"t={int(time.time())},v1={'0' * 64}"
        }
        from app.payments import WebhookVerificationError

        try:
            service.handle_webhook(body, bad_headers)
        except WebhookVerificationError:
            pass
        else:
            raise AssertionError("forged signature must be rejected")
        assert _ledger(db) == []


# --------------------------------------------------------------------------- #
# PayPal gateway (mocked verification endpoint + payloads)
# --------------------------------------------------------------------------- #

class _FakePayPalClient:
    """Replaces the provider's httpx client: canned OAuth + verification SUCCESS."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict | None]] = []

    def post(self, url: str, headers=None, json=None, **kwargs):
        self.posts.append((url, json))
        if url.endswith("/v1/oauth2/token"):
            return _FakeResponse({"access_token": "fake_access_token"})
        if url.endswith("/v1/notifications/verify-webhook-signature"):
            return _FakeResponse({"verification_status": "SUCCESS"})
        return _FakeResponse({})


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


def _paypal_provider() -> "PayPalPaymentProvider":
    from app.payments.paypal import PayPalPaymentProvider

    provider = PayPalPaymentProvider("client_id", "client_secret", "wh_id")
    provider._client = _FakePayPalClient()
    return provider


def _paypal_event(event_type: str, status: str | None, event_id: str) -> bytes:
    # Realistic resource per event family: subscription lifecycle events carry
    # the subscription as the resource; payment (sale) events carry the *sale*
    # (its own id + billing_agreement_id pointing at the subscription) plus a
    # create_time the provider turns into the approximate billing period.
    if event_type.startswith("PAYMENT.SALE"):
        resource = {
            "id": "8PT-sale-1",
            "billing_agreement_id": "sub_pp_1",
            "create_time": NOW.isoformat(),
        }
    else:
        resource = {"id": "sub_pp_1"}
    if status:
        resource["status"] = status
    return json.dumps(
        {
            "id": event_id,
            "event_type": event_type,
            "resource": resource,
            "resource_type": "sale",
        }
    ).encode()


def test_paypal_renewal_success_and_failure_mocked(db_session, monkeypatch):
    from app.payments.base import WebhookEventType

    notifications = []
    monkeypatch.setattr(
        "app.services.subscriptions.enqueue_payment_failed_notification",
        lambda sub_id: notifications.append(sub_id),
    )
    provider = _paypal_provider()
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        sub = _active_subscription(
            db, subscriber.id, creator.id, provider="paypal", external_ref="sub_pp_1"
        )
        sub_id = sub.id
        service = SubscriptionService(db, provider=provider)

        # PAYMENT.SALE.COMPLETED (renewal charged) — verified + normalized.
        completed = _paypal_event("PAYMENT.SALE.COMPLETED", "COMPLETED", "WH-success-1")
        event = service.handle_webhook(completed, {})
        assert event.event_type == WebhookEventType.payment_succeeded
        assert event.id == "WH-success-1"
        db.refresh(sub)
        assert sub.status == SubscriptionStatus.active
        assert notifications == []

        # PAYMENT.SALE.DENIED (renewal failed) -> past_due + one notification.
        denied = _paypal_event("PAYMENT.SALE.DENIED", "SUSPENDED", "WH-denied-1")
        event = service.handle_webhook(denied, {})
        assert event.event_type == WebhookEventType.payment_failed
        db.refresh(sub)
        assert sub.status == SubscriptionStatus.past_due
        assert notifications == [sub_id]

        # Retry of the same denied event -> duplicate, no re-notify.
        event = service.handle_webhook(denied, {})
        assert event.duplicate is True
        assert len(notifications) == 1
        # Verification was actually performed against the mocked endpoint.
        verify_calls = [
            url for url, _ in provider._client.posts
            if url.endswith("/v1/notifications/verify-webhook-signature")
        ]
        assert len(verify_calls) == 3


def test_paypal_capture_refunded_maps_to_payment_refunded(db_session):
    """A one-time capture refund normalizes to payment.refunded with metadata.

    The resource is the *capture* (its id differs from the order id we stored),
    so matching relies on the ``custom_id`` metadata the provider now passes
    through — the service falls back to it when the ref lookup misses.
    """
    from app.payments.base import WebhookEventType

    provider = _paypal_provider()
    body = json.dumps(
        {
            "id": "WH-refund-1",
            "event_type": "PAYMENT.CAPTURE.REFUNDED",
            "resource": {
                "id": "cap_1",
                "custom_id": json.dumps(
                    {"subscriber_id": "3", "post_id": "9"}
                ),
            },
            "resource_type": "capture",
        }
    ).encode()
    event = provider.verify_webhook(body, {})
    assert event.event_type == WebhookEventType.payment_refunded
    assert event.external_ref == "cap_1"
    assert event.metadata == {"subscriber_id": "3", "post_id": "9"}


def test_paypal_verification_failure_rejected(db_session):
    from app.payments import WebhookVerificationError
    from app.payments.paypal import PayPalPaymentProvider

    provider = PayPalPaymentProvider("client_id", "client_secret", "wh_id")

    class _FailingClient:
        def post(self, url, headers=None, json=None, **kwargs):
            if url.endswith("/v1/oauth2/token"):
                return _FakeResponse({"access_token": "fake_access_token"})
            return _FakeResponse({"verification_status": "FAILURE"})

    provider._client = _FailingClient()
    body = _paypal_event("PAYMENT.SALE.DENIED", "SUSPENDED", "WH-denied-2")
    try:
        provider.verify_webhook(body, {})
    except WebhookVerificationError:
        pass
    else:
        raise AssertionError("failed PayPal verification must be rejected")


# --------------------------------------------------------------------------- #
# Endpoint-level (through the app) + notification task
# --------------------------------------------------------------------------- #

def test_concurrent_redelivery_race_treated_as_duplicate(db_session, monkeypatch):
    """A concurrent delivery that committed first is acked as a duplicate, not a 500.

    Simulates the race: another request processed the same event id and
    committed before us, so our commit trips the ledger's unique constraint.
    """
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        sub = _active_subscription(db, subscriber.id, creator.id)
        service = SubscriptionService(db, provider=MockPaymentProvider())

        body = MockPaymentProvider.make_webhook_body(
            "payment.succeeded",
            external_ref=sub.external_ref,
            event_id="evt_race_1",
        )
        headers = MockPaymentProvider.sign_body(body)

        # The other request already marked the event processed (committed).
        db.add(ProcessedWebhookEvent(provider="mock", event_id="evt_race_1"))
        db.commit()
        # …but our dedup check races and still sees it as unprocessed.
        monkeypatch.setattr(service, "_is_processed", lambda p, e: False)

        event = service.handle_webhook(body, headers)
        assert event.duplicate is True
        assert _ledger(db) == [("mock", "evt_race_1")]  # still exactly one marker


def test_notification_enqueue_failure_does_not_fail_webhook(db_session, monkeypatch):
    """A broker outage while enqueuing must not fail the (already committed) webhook."""

    def _boom(sub_id):
        raise RuntimeError("broker down")

    monkeypatch.setattr(
        "app.services.subscriptions.enqueue_payment_failed_notification", _boom
    )
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        sub = _active_subscription(db, subscriber.id, creator.id)
        service = SubscriptionService(db, provider=MockPaymentProvider())

        body = MockPaymentProvider.make_webhook_body(
            "payment.failed",
            external_ref=sub.external_ref,
            event_id="evt_broker_1",
        )
        event = service.handle_webhook(body, MockPaymentProvider.sign_body(body))
        assert event.duplicate is False
        db.refresh(sub)
        assert sub.status == SubscriptionStatus.past_due  # reconciliation persisted
        assert _ledger(db) == [("mock", "evt_broker_1")]


def test_webhook_endpoint_returns_duplicate_flag(client, db_session):
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        sub = _active_subscription(db, subscriber.id, creator.id)
        external_ref = sub.external_ref

    body = MockPaymentProvider.make_webhook_body(
        "payment.succeeded",
        external_ref=external_ref,
        event_id="evt_endpoint_1",
    )
    headers = MockPaymentProvider.sign_body(body)
    headers["Content-Type"] = "application/json"

    first = client.post("/webhooks/mock", data=body, headers=headers)
    assert first.status_code == 200
    assert first.json()["duplicate"] is False

    second = client.post("/webhooks/mock", data=body, headers=headers)
    assert second.status_code == 200
    assert second.json()["duplicate"] is True


def test_notify_task_logs_when_smtp_unconfigured(db_session, monkeypatch):
    """No SMTP configured -> task runs cleanly and degrades to a log line."""
    from app.tasks import notify_payment_failed

    monkeypatch.setattr("app.config.settings.SMTP_HOST", "")
    # The task opens its own session — point it at the test DB.
    monkeypatch.setattr("app.tasks.SessionLocal", lambda: db_session)
    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        sub = _active_subscription(db, subscriber.id, creator.id)
        sub_id = sub.id

    assert notify_payment_failed.run(sub_id) is False


def test_notify_task_sends_email_when_smtp_configured(db_session, monkeypatch):
    """SMTP configured -> the worker sends the grace-period email."""
    from app.tasks import notify_payment_failed

    monkeypatch.setattr("app.config.settings.SMTP_HOST", "smtp.test")
    monkeypatch.setattr("app.config.settings.SMTP_PORT", 587)
    monkeypatch.setattr("app.config.settings.SMTP_USERNAME", "user")
    monkeypatch.setattr("app.config.settings.SMTP_PASSWORD", "pass")
    monkeypatch.setattr("app.config.settings.SMTP_FROM", "noreply@example.com")
    monkeypatch.setattr("app.config.settings.SMTP_TLS", True)

    sent: list = []

    class _FakeSMTP:
        def __init__(self, *args, **kwargs):
            self.entered = False

        def __enter__(self):
            self.entered = True
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def send_message(self, msg):
            sent.append(msg)

    monkeypatch.setattr("app.tasks.smtplib.SMTP", _FakeSMTP)
    # The task opens its own session — point it at the test DB.
    monkeypatch.setattr("app.tasks.SessionLocal", lambda: db_session)

    with db_session as db:
        subscriber, creator = _subscriber_and_creator(db)
        sub = _active_subscription(db, subscriber.id, creator.id)
        sub_id = sub.id

    assert notify_payment_failed.run(sub_id) is True
    assert len(sent) == 1
    assert sent[0]["To"] == "sub@example.com"
    assert "past-due" in sent[0].get_content().lower() or "grace" in sent[0].get_content().lower()
