"""In-memory mock payment provider.

Used for development and unit tests: no network, fully deterministic, and it
behaves like a real gateway (creates subscriptions with ids + checkout urls,
cancels them, verifies signed webhooks). ``verify_webhook`` authenticates the
body with an HMAC signature (``X-Mock-Signature`` header) so webhook-handling
code paths are exercised realistically.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Mapping

from .base import (
    ChargeRequest,
    ChargeResult,
    PaymentLinkResult,
    PaymentProvider,
    SubscriptionIntent,
    SubscriptionResult,
    WebhookEvent,
    WebhookEventType,
    WebhookVerificationError,
)

_MOCK_EVENT_MAP = {
    "subscription.created": WebhookEventType.subscription_created,
    "subscription.updated": WebhookEventType.subscription_updated,
    "subscription.canceled": WebhookEventType.subscription_canceled,
    "payment.succeeded": WebhookEventType.payment_succeeded,
    "payment.failed": WebhookEventType.payment_failed,
    "payment.refunded": WebhookEventType.payment_refunded,
}


class MockPaymentProvider(PaymentProvider):
    """Deterministic in-memory provider for dev and tests.

    Exposes ``subscriptions`` / ``charges`` (by external ref) so tests can
    assert on the state the provider would have stored server-side.
    """

    name = "mock"

    def __init__(self, webhook_secret: str = "mock-webhook-secret") -> None:
        self.webhook_secret = webhook_secret
        self.subscriptions: dict[str, dict] = {}
        self.charges: dict[str, dict] = {}
        self.one_time_links: dict[str, dict] = {}
        self._seq = 0

    # ------------------------------------------------------------------ #
    # Interface
    # ------------------------------------------------------------------ #

    def create_customer(
        self, email: str, name: str | None = None, metadata: dict | None = None
    ) -> str:
        self._seq += 1
        customer_ref = f"cus_mock_{self._seq}"
        return customer_ref

    def create_subscription(self, intent: SubscriptionIntent) -> SubscriptionResult:
        self._seq += 1
        # uuid fragment guarantees uniqueness across worker processes (each
        # gunicorn worker has its own in-memory counter), so refs never collide
        # under ``uq_subscription_provider_ref``.
        external_ref = f"sub_mock_{self._seq}_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        self.subscriptions[external_ref] = {
            "external_ref": external_ref,
            "plan_id": intent.plan_id,
            "status": "active",
            "current_period_start": now,
            "current_period_end": now + timedelta(days=30),
            "metadata": intent.metadata,
        }
        return SubscriptionResult(
            external_ref=external_ref,
            status="active",
            checkout_url=f"https://mock.checkout/{external_ref}",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            raw=dict(self.subscriptions[external_ref]),
        )

    def cancel_subscription(self, external_ref: str) -> None:
        # Cancellation is idempotent like real gateways: an unknown ref is a
        # no-op (it is effectively already gone).
        sub = self.subscriptions.get(external_ref)
        if sub is None:
            return
        sub["status"] = "canceled"

    def cancel_at_period_end(self, external_ref: str) -> None:
        sub = self.subscriptions.get(external_ref)
        if sub is None:
            return
        sub["cancel_at_period_end"] = True

    def verify_webhook(
        self, body: bytes, headers: Mapping[str, str]
    ) -> WebhookEvent:
        # Headers are case-insensitive (HTTP servers lower-case them).
        signature = next(
            (
                v
                for k, v in headers.items()
                if k.lower() == "x-mock-signature"
            ),
            "",
        )
        expected = self._signature(body)
        if not hmac.compare_digest(signature, expected):
            raise WebhookVerificationError("Invalid X-Mock-Signature header")

        payload = json.loads(body)
        event_name = payload.get("type", "")
        event_type = _MOCK_EVENT_MAP.get(event_name)
        if event_type is None:
            raise WebhookVerificationError(f"Unknown mock event type: {event_name}")

        return WebhookEvent(
            provider=self.name,
            event_type=event_type,
            external_ref=payload.get("external_ref"),
            id=payload.get("id"),
            subscription_status=payload.get("subscription_status"),
            metadata=payload.get("metadata", {}),
            raw=payload,
        )

    def create_one_time_link(self, request: ChargeRequest) -> PaymentLinkResult:
        """Create a hosted one-time payment link.

        The mock never charges synchronously — tests simulate the hosted
        payment by posting a signed ``payment.succeeded`` webhook for the link
        ref (exactly like a real gateway's completion event), which activates
        the unlock through the normal webhook path.
        """
        self._seq += 1
        external_ref = f"ch_mock_{self._seq}_{uuid.uuid4().hex[:8]}"
        self.one_time_links[external_ref] = {
            "external_ref": external_ref,
            "amount_cents": request.amount_cents,
            "currency": request.currency,
            "metadata": request.metadata,
        }
        return PaymentLinkResult(
            external_ref=external_ref,
            checkout_url=f"https://mock.checkout/{external_ref}",
            raw=dict(self.one_time_links[external_ref]),
        )

    def charge_one_time(self, request: ChargeRequest) -> ChargeResult:
        self._seq += 1
        external_ref = f"ch_mock_{self._seq}"
        self.charges[external_ref] = {
            "external_ref": external_ref,
            "amount_cents": request.amount_cents,
            "currency": request.currency,
            "status": "succeeded",
            "metadata": request.metadata,
        }
        return ChargeResult(
            external_ref=external_ref,
            status="succeeded",
            amount_cents=request.amount_cents,
            currency=request.currency,
            raw=dict(self.charges[external_ref]),
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _signature(self, body: bytes) -> str:
        return hmac.new(
            self.webhook_secret.encode(), body, hashlib.sha256
        ).hexdigest()

    @classmethod
    def sign_body(
        cls, body: bytes, webhook_secret: str = "mock-webhook-secret"
    ) -> dict[str, str]:
        """Sign a webhook body the way the mock gateway would — for tests."""
        return {"X-Mock-Signature": cls(webhook_secret)._signature(body)}

    @classmethod
    def make_webhook_body(
        cls,
        event_type: str,
        external_ref: str | None = None,
        subscription_status: str | None = None,
        metadata: dict | None = None,
        event_id: str | None = None,
    ) -> bytes:
        """Build a signed-able webhook body for tests.

        ``event_id`` simulates the provider's unique event id (real gateways
        always send one); when omitted the event carries no id, which the
        service treats as not-dedup-able (compat with legacy call sites).
        """
        payload = {
            "type": event_type,
            "external_ref": external_ref,
            "subscription_status": subscription_status,
            "metadata": metadata or {},
            "timestamp": time.time(),
        }
        if event_id is not None:
            payload["id"] = event_id
        return json.dumps(payload).encode()

    @classmethod
    def from_settings(cls, settings) -> "MockPaymentProvider":
        return cls()
