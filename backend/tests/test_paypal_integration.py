"""PayPal Subscriptions integration tests.

The real PayPal REST API is simulated with ``httpx.MockTransport`` so the
provider's real request-building, OAuth, and webhook-verification code runs
against a faithful fake of the sandbox Billing Subscriptions API (products,
plans, subscriptions with the approve link, cancel, and the webhook signature
verification endpoint). Covers the acceptance criteria:

- ``create_plan`` bootstraps an ACTIVE monthly plan (product + plan);
- subscribing creates an ``incomplete`` local row with the hosted **approve**
  link and the subscription ref (``I-...``);
- the ``BILLING.SUBSCRIPTION.APPROVED`` webhook activates the subscription;
- renewal webhooks (``PAYMENT.SALE.COMPLETED`` / ``DENIED``) reconcile the
  subscription **by ``billing_agreement_id``** (the sale's own id differs from
  the stored ref) — the ``webhook updates correctly`` acceptance;
- cancel webhooks and cancellation via the service work.

A real-sandbox test (``test_paypal_sandbox_subscribe_flow``) runs only when
actual sandbox credentials are provided (``PAYPAL_CLIENT_ID`` /
``PAYPAL_CLIENT_SECRET`` / ``PAYPAL_WEBHOOK_ID`` **and** ``RUN_PAYPAL_SANDBOX=1``);
it creates a plan + subscription against the real sandbox and asserts the local
row, leaving the browser approval + webhook reconciliation to the simulated
tests above.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.models import (
    ProcessedWebhookEvent,
    Subscription,
    SubscriptionStatus,
    User,
    UserRole,
)
from app.payments import ProviderConfigurationError, WebhookVerificationError
from app.payments.paypal import PayPalPaymentProvider
from app.services.subscriptions import SubscriptionService


# --------------------------------------------------------------------------- #
# A fake PayPal sandbox (httpx.MockTransport handler)
# --------------------------------------------------------------------------- #

class FakePayPalAPI:
    """In-memory PayPal: OAuth, products, billing plans, subscriptions."""

    def __init__(self) -> None:
        self.products: dict[str, dict] = {}
        self.plans: dict[str, dict] = {}
        self.subscriptions: dict[str, dict] = {}
        self.verification_status = "SUCCESS"
        self._seq = 0

    # -- state helpers (test driving) ------------------------------------- #

    def activate_subscription(self, subscription_id: str) -> None:
        """Simulate the buyer approving + first payment: APPROVAL_PENDING -> ACTIVE."""
        self.subscriptions[subscription_id]["status"] = "ACTIVE"

    # -- httpx.MockTransport handler -------------------------------------- #

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/oauth2/token":
            return self._oauth_token()
        if request.method == "POST" and path == "/v1/catalogs/products":
            return self._create_product(request)
        if request.method == "POST" and path == "/v1/billing/plans":
            return self._create_plan(request)
        if request.method == "POST" and path == "/v1/billing/subscriptions":
            return self._create_subscription(request)
        if request.method == "POST" and path.endswith("/activate"):
            return self._activate(request)
        if request.method == "POST" and path.endswith("/cancel"):
            return self._cancel(request)
        if request.method == "POST" and path == "/v1/notifications/verify-webhook-signature":
            return httpx.Response(200, json={"verification_status": self.verification_status})
        return httpx.Response(404, text='{"name": "RESOURCE_NOT_FOUND"}')

    def _oauth_token(self) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "fake_access_token",
                "token_type": "Bearer",
                "expires_in": 32400,
            },
        )

    def _create_product(self, request: httpx.Request) -> httpx.Response:
        self._seq += 1
        body = json.loads(request.content)
        product = {
            "id": f"PROD-{self._seq}",
            "name": body.get("name", ""),
            "type": body.get("type", ""),
            "category": body.get("category", ""),
        }
        self.products[product["id"]] = product
        return httpx.Response(200, json=product)

    def _create_plan(self, request: httpx.Request) -> httpx.Response:
        self._seq += 1
        body = json.loads(request.content)
        plan = {
            "id": f"P-{self._seq}",
            "product_id": body.get("product_id"),
            "name": body.get("name", ""),
            "status": "ACTIVE",  # plans created via the API are immediately usable
            "billing_cycles": body.get("billing_cycles", []),
            "payment_preferences": body.get("payment_preferences", {}),
            "create_time": datetime.now(timezone.utc).isoformat(),
        }
        self.plans[plan["id"]] = plan
        return httpx.Response(200, json=plan)

    def _create_subscription(self, request: httpx.Request) -> httpx.Response:
        self._seq += 1
        body = json.loads(request.content)
        sub_id = f"I-{self._seq}"
        now = datetime.now(timezone.utc)
        sub = {
            "id": sub_id,
            "plan_id": body.get("plan_id"),
            "status": "APPROVAL_PENDING",
            "subscriber": body.get("subscriber", {}),
            "custom_id": body.get("custom_id", ""),
            "create_time": now.isoformat(),
            "status_update_time": now.isoformat(),
            "links": [
                {
                    "rel": "approve",
                    "href": (
                        "https://www.sandbox.paypal.com/webapps/billing/subscriptions"
                        f"?ba_token={sub_id}"
                    ),
                },
                {
                    "rel": "self",
                    "href": (
                        "https://api-m.sandbox.paypal.com"
                        f"/v1/billing/subscriptions/{sub_id}"
                    ),
                },
            ],
        }
        self.subscriptions[sub_id] = sub
        return httpx.Response(200, json=sub)

    def _activate(self, request: httpx.Request) -> httpx.Response:
        sub_id = request.url.path.split("/")[-2]
        self.subscriptions[sub_id]["status"] = "ACTIVE"
        return httpx.Response(204)

    def _cancel(self, request: httpx.Request) -> httpx.Response:
        sub_id = request.url.path.split("/")[-2]
        self.subscriptions[sub_id]["status"] = "CANCELLED"
        return httpx.Response(204)


def _paypal_provider(fake_api: FakePayPalAPI) -> PayPalPaymentProvider:
    return PayPalPaymentProvider(
        client_id="client_id",
        client_secret="client_secret",
        webhook_id="wh_id",
        environment="sandbox",
        transport=httpx.MockTransport(fake_api.handle),
    )


def _paypal_webhook(event_type: str, resource: dict, event_id: str | None = None) -> bytes:
    payload: dict = {
        "id": event_id or f"WH-{event_type}-{resource.get('id', '')}",
        "event_type": event_type,
        "resource": resource,
        "resource_type": "subscription",
        "summary": event_type,
    }
    return json.dumps(payload).encode()


def _create_users(db):
    subscriber = User(
        email="paypal-sub@example.com",
        username="paypal-sub",
        hashed_password="not-used-in-tests",
        role=UserRole.registered,
        is_active=True,
    )
    creator = User(
        email="paypal-creator@example.com",
        username="paypal-creator",
        hashed_password="not-used-in-tests",
        role=UserRole.creator,
        is_active=True,
    )
    db.add_all([subscriber, creator])
    db.commit()
    db.refresh(subscriber)
    db.refresh(creator)
    return subscriber, creator


def _subscribe(db, provider, plan_id: str):
    """Subscribe the fixture users; returns (row, sub id, hosted approve url)."""
    subscriber, creator = _create_users(db)
    service = SubscriptionService(db, provider=provider)
    subscription = service.create_subscription(
        subscriber.id,
        creator.id,
        plan_id=plan_id,
        success_url="https://example.com/success",
        cancel_url="https://example.com/cancel",
    )
    db.refresh(subscription)
    assert subscription.status == SubscriptionStatus.incomplete
    return subscription, subscription.external_ref, subscription.checkout_url


# --------------------------------------------------------------------------- #
# Configuration guards
# --------------------------------------------------------------------------- #

def test_paypal_environment_must_be_sandbox_or_live():
    with pytest.raises(ProviderConfigurationError):
        PayPalPaymentProvider(
            client_id="a",
            client_secret="b",
            webhook_id="c",
            environment="mars",
        )


def test_paypal_rejects_non_positive_plan_price():
    fake_api = FakePayPalAPI()
    provider = _paypal_provider(fake_api)
    with pytest.raises(ProviderConfigurationError):
        provider.create_plan("Free tier", 0)


def test_paypal_reuses_configured_product_id():
    """A configured PAYPAL_PRODUCT_ID is used without creating a new product."""
    fake_api = FakePayPalAPI()
    provider = PayPalPaymentProvider(
        client_id="a",
        client_secret="b",
        webhook_id="c",
        environment="sandbox",
        product_id="PROD-existing",
        transport=httpx.MockTransport(fake_api.handle),
    )
    plan = provider.create_plan("Monthly Tier", 500)
    assert plan["product_id"] == "PROD-existing"
    assert fake_api.products == {}  # no product-create call was made


# --------------------------------------------------------------------------- #
# Plan bootstrap (product + ACTIVE monthly plan)
# --------------------------------------------------------------------------- #

def test_paypal_creates_active_monthly_plan():
    fake_api = FakePayPalAPI()
    provider = _paypal_provider(fake_api)

    plan = provider.create_plan("Monthly Tier", 500, currency="usd")
    assert plan["id"].startswith("P-")
    assert plan["status"] == "ACTIVE"
    assert plan["product_id"].startswith("PROD-")

    # Monthly, infinite, fixed $5.00 with auto-bill on failure.
    [cycle] = plan["billing_cycles"]
    assert cycle["frequency"] == {"interval_unit": "MONTH", "interval_count": 1}
    assert cycle["total_cycles"] == 0
    assert cycle["pricing_scheme"]["fixed_price"] == {
        "value": "5.00",
        "currency_code": "usd",
    }
    assert plan["payment_preferences"]["auto_bill_outstanding"] is True
    # A second plan reuses the same product (created once).
    plan2 = provider.create_plan("Another tier", 900)
    assert plan2["product_id"] == plan["product_id"]
    assert len(fake_api.products) == 1


# --------------------------------------------------------------------------- #
# Subscribe flow: pending row + hosted approve link
# --------------------------------------------------------------------------- #

def test_paypal_subscribe_creates_pending_subscription_with_approve_link(db_session):
    fake_api = FakePayPalAPI()
    provider = _paypal_provider(fake_api)
    plan_id = provider.create_plan("Monthly Tier", 500)["id"]

    with db_session as db:
        subscription, sub_id, approve_url = _subscribe(db, provider, plan_id)

        assert subscription.payment_provider == "paypal"
        assert subscription.status == SubscriptionStatus.incomplete
        assert sub_id.startswith("I-")
        assert approve_url.startswith("https://www.sandbox.paypal.com/webapps/billing/")
        assert subscription.checkout_url == approve_url
        # PayPal doesn't return a billing period at creation; open-ended until
        # the first payment webhook.
        assert subscription.current_period_end is None

        # The request carried our metadata + subscriber email to the gateway.
        remote = fake_api.subscriptions[sub_id]
        meta = json.loads(remote["custom_id"])
        assert meta["subscriber_id"] == str(subscription.subscriber_id)
        assert meta["creator_id"] == str(subscription.creator_id)
        assert remote["subscriber"]["email_address"] == "paypal-sub@example.com"
        assert remote["plan_id"] == plan_id


# --------------------------------------------------------------------------- #
# Acceptance: approve -> active, then renewals reconcile by billing_agreement_id
# --------------------------------------------------------------------------- #

def test_paypal_approval_webhook_activates_subscription(db_session, monkeypatch):
    """Subscribing via PayPal + the buyer's approval = an active subscription."""
    notifications: list = []
    monkeypatch.setattr(
        "app.services.subscriptions.enqueue_payment_failed_notification",
        lambda sub_id: notifications.append(sub_id),
    )
    fake_api = FakePayPalAPI()
    provider = _paypal_provider(fake_api)
    plan_id = provider.create_plan("Monthly Tier", 500)["id"]

    with db_session as db:
        subscription, sub_id, _ = _subscribe(db, provider, plan_id)
        service = SubscriptionService(db, provider=provider)

        # Buyer approves in the hosted flow; PayPal fires APPROVED then ACTIVATED.
        fake_api.activate_subscription(sub_id)
        for event_type, status in (("BILLING.SUBSCRIPTION.APPROVED", "APPROVED"),
                                   ("BILLING.SUBSCRIPTION.ACTIVATED", "ACTIVE")):
            resource = dict(fake_api.subscriptions[sub_id], status=status)
            body = _paypal_webhook(event_type, resource, event_id=f"WH-{event_type}-1")
            event = service.handle_webhook(body, {})
            assert event.duplicate is False

        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        assert subscription.checkout_url is None  # no longer pending
        assert notifications == []

        # Approval stamped the approximate 30-day cycle (open access window).
        # SQLite stores naive UTC datetimes; normalize before comparing.
        assert subscription.current_period_end is not None
        period_end = subscription.current_period_end
        if period_end.tzinfo is None:
            period_end = period_end.replace(tzinfo=timezone.utc)
        assert period_end > datetime.now(timezone.utc)


def test_paypal_renewal_webhook_reconciles_by_billing_agreement_id(db_session, monkeypatch):
    """A renewal sale's id ≠ the stored ref; billing_agreement_id drives the lookup.

    Regression guard: before the fix the sale id (``8PT...``) became the
    external_ref, the subscription lookup missed, and renewals silently no-oped.
    """
    notifications: list = []
    monkeypatch.setattr(
        "app.services.subscriptions.enqueue_payment_failed_notification",
        lambda sub_id: notifications.append(sub_id),
    )
    fake_api = FakePayPalAPI()
    provider = _paypal_provider(fake_api)
    plan_id = provider.create_plan("Monthly Tier", 500)["id"]

    with db_session as db:
        subscription, sub_id, _ = _subscribe(db, provider, plan_id)
        service = SubscriptionService(db, provider=provider)
        fake_api.activate_subscription(sub_id)
        approved = dict(fake_api.subscriptions[sub_id], status="APPROVED")
        service.handle_webhook(
            _paypal_webhook("BILLING.SUBSCRIPTION.APPROVED", approved, "WH-APPROVED-x"),
            {},
        )
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active

        # First renewal fails (SALE.DENIED) -> past_due, then a later renewal
        # succeeds (SALE.COMPLETED) -> back to active. Both carry a *sale* id
        # and the subscription id as billing_agreement_id.
        period_end_before_failure = subscription.current_period_end
        denied = {
            "id": "8PT1111222233334444",  # sale id, NOT the stored sub ref
            "billing_agreement_id": sub_id,
            "status": "DENIED",
            "create_time": datetime.now(timezone.utc).isoformat(),
        }
        event = service.handle_webhook(
            _paypal_webhook("PAYMENT.SALE.DENIED", denied, "WH-SALE-DENIED-1"), {}
        )
        assert event.duplicate is False
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.past_due
        # A failed renewal must NOT stamp a future 30-day window (the cycle
        # never started) — the approval-stamped period is unchanged.
        assert subscription.current_period_end == period_end_before_failure
        assert notifications == [subscription.id]

        completed = {
            "id": "8PT5555666677778888",  # a different sale id
            "billing_agreement_id": sub_id,
            "status": "COMPLETED",
            "create_time": datetime.now(timezone.utc).isoformat(),
        }
        service.handle_webhook(
            _paypal_webhook("PAYMENT.SALE.COMPLETED", completed, "WH-SALE-COMPLETED-1"), {}
        )
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        # The renewal's create_time drove the new 30-day period.
        assert subscription.current_period_end is not None
        assert len(notifications) == 1  # notify only on the transition to past_due


def test_paypal_renewal_redelivery_is_duplicate(db_session):
    """A provider retry of the same renewal event is acked, not re-applied."""
    fake_api = FakePayPalAPI()
    provider = _paypal_provider(fake_api)
    plan_id = provider.create_plan("Monthly Tier", 500)["id"]

    with db_session as db:
        subscription, sub_id, _ = _subscribe(db, provider, plan_id)
        service = SubscriptionService(db, provider=provider)
        body = _paypal_webhook(
            "PAYMENT.SALE.COMPLETED",
            {"id": "8PT1", "billing_agreement_id": sub_id, "status": "COMPLETED"},
            "WH-SALE-DUP-1",
        )
        first = service.handle_webhook(body, {})
        second = service.handle_webhook(body, {})
        assert first.duplicate is False
        assert second.duplicate is True
        # Exactly one ledger marker for the pair.
        markers = db.scalars(
            select(ProcessedWebhookEvent).where(
                ProcessedWebhookEvent.provider == "paypal",
                ProcessedWebhookEvent.event_id == "WH-SALE-DUP-1",
            )
        ).all()
        assert len(markers) == 1


# --------------------------------------------------------------------------- #
# Cancel / suspend
# --------------------------------------------------------------------------- #

def test_paypal_cancel_webhook_sets_canceled(db_session):
    fake_api = FakePayPalAPI()
    provider = _paypal_provider(fake_api)
    plan_id = provider.create_plan("Monthly Tier", 500)["id"]

    with db_session as db:
        subscription, sub_id, _ = _subscribe(db, provider, plan_id)
        service = SubscriptionService(db, provider=provider)

        body = _paypal_webhook(
            "BILLING.SUBSCRIPTION.CANCELLED",
            {"id": sub_id, "status": "CANCELLED"},
            "WH-CANCELLED-1",
        )
        service.handle_webhook(body, {})
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.canceled


def test_paypal_cancel_via_service_marks_gateway_and_row(db_session):
    fake_api = FakePayPalAPI()
    provider = _paypal_provider(fake_api)
    plan_id = provider.create_plan("Monthly Tier", 500)["id"]

    with db_session as db:
        subscription, sub_id, _ = _subscribe(db, provider, plan_id)
        service = SubscriptionService(db, provider=provider)

        service.cancel_subscription(subscription)
        assert fake_api.subscriptions[sub_id]["status"] == "CANCELLED"
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.canceled


# --------------------------------------------------------------------------- #
# Verification failures
# --------------------------------------------------------------------------- #

def test_paypal_webhook_verification_failure_rejected(db_session):
    fake_api = FakePayPalAPI()
    fake_api.verification_status = "FAILURE"
    provider = _paypal_provider(fake_api)
    body = _paypal_webhook(
        "BILLING.SUBSCRIPTION.CANCELLED",
        {"id": "I-1", "status": "CANCELLED"},
    )
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook(body, {})


def test_paypal_webhook_unknown_event_rejected(db_session):
    fake_api = FakePayPalAPI()
    provider = _paypal_provider(fake_api)
    body = _paypal_webhook("NOT.A.REAL.EVENT", {"id": "I-1"})
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook(body, {})


# --------------------------------------------------------------------------- #
# Real sandbox (opt-in — requires actual sandbox credentials)
# --------------------------------------------------------------------------- #

_SANDBOX_CREDS = all(
    os.environ.get(var)
    for var in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_WEBHOOK_ID")
)
_REQUIRE_SANDBOX = os.environ.get("RUN_PAYPAL_SANDBOX") == "1"


@pytest.mark.skipif(
    not (_SANDBOX_CREDS and _REQUIRE_SANDBOX),
    reason=(
        "Real PayPal sandbox test: set PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET / "
        "PAYPAL_WEBHOOK_ID (a sandbox REST app) and RUN_PAYPAL_SANDBOX=1. The buyer "
        "must open the returned approve link and approve; webhook reconciliation "
        "is exercised by the simulated tests above."
    ),
)
def test_paypal_sandbox_subscribe_flow(db_session):
    """Real sandbox: bootstrap a plan and subscribe against api-m.sandbox.paypal.com.

    Assertions stop at the pending row + hosted approve link — the approval step
    is a human/browser action against PayPal's hosted flow; once approved, the
    same webhook pipeline tested above reconciles the subscription.
    """
    from app.config import settings
    from app.payments.paypal import _SANDBOX_BASE  # private, but asserted env only

    provider = PayPalPaymentProvider(
        client_id=os.environ["PAYPAL_CLIENT_ID"],
        client_secret=os.environ["PAYPAL_CLIENT_SECRET"],
        webhook_id=os.environ["PAYPAL_WEBHOOK_ID"],
        environment="sandbox",
    )
    assert provider._client.base_url == _SANDBOX_BASE

    plan = provider.create_plan(
        name=f"Sandbox test plan {datetime.now().timestamp():.0f}",
        price_cents=settings.SUBSCRIPTION_TIER_PRICE_CENTS,
        currency="usd",
    )
    assert plan["id"].startswith("P-")

    with db_session as db:
        subscription, sub_id, approve_url = _subscribe(db, provider, plan["id"])
        assert subscription.payment_provider == "paypal"
        assert sub_id.startswith("I-")
        assert "paypal.com" in approve_url
        print(
            f"\n[paypal sandbox] approve the subscription at: {approve_url}\n"
            f"[paypal sandbox] then configure the webhook for event types\n"
            f"    BILLING.SUBSCRIPTION.APPROVED / ACTIVATED / CANCELLED,\n"
            f"    PAYMENT.SALE.COMPLETED / DENIED\n"
            f"[paypal sandbox] and point it at POST /api/webhooks/paypal"
        )
