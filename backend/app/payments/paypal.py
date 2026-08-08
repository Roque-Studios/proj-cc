"""PayPal payment gateway implementation.

Talks to the PayPal REST API over httpx (no SDK dependency). Uses OAuth2 client
credentials for auth, the Billing Subscriptions API for subscriptions and
cancellation, the webhook signature verification endpoint for webhooks, and
Orders v2 for one-time charges.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Mapping

import httpx

from .base import (
    ChargeRequest,
    ChargeResult,
    PaymentLinkResult,
    PaymentProvider,
    PaymentProviderError,
    ProviderConfigurationError,
    SubscriptionIntent,
    SubscriptionResult,
    WebhookEvent,
    WebhookEventType,
    WebhookVerificationError,
)

_SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
_LIVE_BASE = "https://api-m.paypal.com"

# PayPal subscription status -> our normalized status vocabulary.
_STATUS_MAP = {
    "APPROVAL_PENDING": "trialing",
    "APPROVED": "active",
    "ACTIVE": "active",
    "SUSPENDED": "past_due",
    "CANCELLED": "canceled",
    "EXPIRED": "expired",
}

# PayPal event name -> normalized webhook event type.
_EVENT_MAP = {
    "BILLING.SUBSCRIPTION.ACTIVATED": WebhookEventType.subscription_updated,
    "BILLING.SUBSCRIPTION.APPROVED": WebhookEventType.subscription_created,
    "BILLING.SUBSCRIPTION.CANCELLED": WebhookEventType.subscription_canceled,
    "BILLING.SUBSCRIPTION.EXPIRED": WebhookEventType.subscription_canceled,
    "BILLING.SUBSCRIPTION.SUSPENDED": WebhookEventType.subscription_updated,
    "PAYMENT.SALE.COMPLETED": WebhookEventType.payment_succeeded,
    "PAYMENT.SALE.DENIED": WebhookEventType.payment_failed,
    # One-time Orders v2 captures/refunds (the capture's ``custom_id`` carries
    # the charge metadata we stamped, used to match the local unlock row).
    "PAYMENT.CAPTURE.REFUNDED": WebhookEventType.payment_refunded,
    "PAYMENT.REFUND.COMPLETED": WebhookEventType.payment_refunded,
    # A refunded subscription renewal: the charge is refunded but the
    # subscription itself is untouched. Routed to the refund path, where it is
    # a safe no-op (the sale carries no post metadata to match an unlock) — so
    # PayPal receives a 200 instead of a 400-triggered retry loop.
    "PAYMENT.SALE.REFUNDED": WebhookEventType.payment_refunded,
}


def _normalize_status(status: str | None) -> str:
    return _STATUS_MAP.get(status or "", "active")


def _iso_to_dt(value: str | None) -> datetime | None:
    """Parse a PayPal RFC3339 timestamp (e.g. ``2026-08-06T12:00:00Z``) to aware UTC."""
    if not value:
        return None
    try:
        # fromisoformat accepts ``+00:00``; normalize the trailing ``Z``.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


class PayPalPaymentProvider(PaymentProvider):
    name = "paypal"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        webhook_id: str,
        environment: str = "sandbox",
        timeout: float = 30.0,
        product_id: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not client_id or not client_secret:
            raise ProviderConfigurationError(
                "PayPal client id and secret are required"
            )
        if not webhook_id:
            raise ProviderConfigurationError("PayPal webhook id is required")
        if environment not in ("sandbox", "live"):
            raise ProviderConfigurationError(
                f"PAYPAL_ENVIRONMENT must be 'sandbox' or 'live', got '{environment}'"
            )
        base = _LIVE_BASE if environment == "live" else _SANDBOX_BASE
        self.client_id = client_id
        self.client_secret = client_secret
        self.webhook_id = webhook_id
        self.environment = environment
        # Existing product to attach billing plans to (created via the bootstrap
        # script or the PayPal dashboard); created lazily when absent.
        self.product_id = product_id or None
        # ``transport`` is injected in tests (httpx.MockTransport) to simulate
        # the PayPal API; in production it is None and real HTTP is used.
        self._client = httpx.Client(base_url=base, timeout=timeout, transport=transport)
        self._access_token: str | None = None

    # ------------------------------------------------------------------ #
    # Billing-plan bootstrap (product + monthly plan)
    # ------------------------------------------------------------------ #

    def create_plan(
        self,
        name: str,
        price_cents: int,
        currency: str = "usd",
        product_id: str | None = None,
    ) -> dict:
        """Create an ACTIVE fixed-price monthly billing plan (usable immediately).

        PayPal subscriptions require a billing plan that exists at the gateway,
        so a fresh sandbox (or production) account needs one before
        ``/v1/billing/subscriptions`` will accept ``plan_id``. The plan is
        created under ``product_id`` (or a product created on demand) and the
        returned id (``P-...``) is what the operator sets as
        ``SUBSCRIPTION_TIER_PLAN_ID``. Returns the full plan object.
        """
        if price_cents <= 0:
            raise ProviderConfigurationError(
                "Plan price must be a positive amount in cents"
            )
        product = self._ensure_product(product_id)
        body = {
            "product_id": product["id"],
            "name": name,
            "billing_cycles": [
                {
                    "frequency": {
                        "interval_unit": "MONTH",
                        "interval_count": 1,
                    },
                    "tenure_type": "REGULAR",
                    "sequence": 1,
                    "total_cycles": 0,  # 0 = infinite (monthly, until canceled)
                    "pricing_scheme": {
                        "fixed_price": {
                            "value": f"{price_cents / 100:.2f}",
                            "currency_code": currency,
                        }
                    },
                }
            ],
            "payment_preferences": {
                "auto_bill_outstanding": True,
                "payment_failure_threshold": 1,
            },
        }
        resp = self._client.post(
            "/v1/billing/plans", headers=self._auth_headers(), json=body
        )
        self._raise_for_status(resp)
        return resp.json()

    def _ensure_product(self, product_id: str | None = None) -> dict:
        """Return an existing product or create one (cached on the provider)."""
        product_id = product_id or self.product_id
        if product_id:
            return {"id": product_id}
        body = {
            "name": "Content Creator Engine",
            "description": "Creator subscriptions and one-time content unlocks",
            "type": "SERVICE",
            "category": "SOFTWARE",
        }
        resp = self._client.post(
            "/v1/catalogs/products", headers=self._auth_headers(), json=body
        )
        self._raise_for_status(resp)
        product = resp.json()
        self.product_id = product["id"]  # reuse for future plans
        return product

    # ------------------------------------------------------------------ #
    # Interface
    # ------------------------------------------------------------------ #

    def create_customer(
        self, email: str, name: str | None = None, metadata: dict | None = None
    ) -> str:
        """PayPal has no explicit customer-create endpoint; use the email as the ref."""
        return f"pp_customer_{email}"

    def create_subscription(self, intent: SubscriptionIntent) -> SubscriptionResult:
        body = {
            "plan_id": intent.plan_id,
            "application_context": {
                "brand_name": "Content Creator Engine",
                "user_action": "SUBSCRIBE_NOW",
                "return_url": intent.success_url or "https://example.com/success",
                "cancel_url": intent.cancel_url or "https://example.com/cancel",
            },
            "custom_id": json.dumps(intent.metadata),
        }
        if intent.subscriber_email:
            body["subscriber"] = {"email_address": intent.subscriber_email}

        resp = self._client.post(
            "/v1/billing/subscriptions",
            headers=self._auth_headers(),
            json=body,
        )
        self._raise_for_status(resp)
        sub = resp.json()
        checkout_url = None
        for link in sub.get("links", []):
            if link.get("rel") == "approve":
                checkout_url = link.get("href")
        return SubscriptionResult(
            external_ref=sub["id"],
            status=_normalize_status(sub.get("status")),
            checkout_url=checkout_url,
            raw=sub,
        )

    def cancel_subscription(self, external_ref: str) -> None:
        resp = self._client.post(
            f"/v1/billing/subscriptions/{external_ref}/cancel",
            headers=self._auth_headers(),
            json={"reason": "Canceled by user"},
        )
        self._raise_for_status(resp)

    def cancel_at_period_end(self, external_ref: str) -> None:
        """PayPal has no cancel-at-period-end concept; best-effort no-op.

        The local ``cancel_at_period_end`` flag drives access revocation via
        the scheduled expiry task, so PayPal does not need to do anything.
        """

    def verify_webhook(
        self, body: bytes, headers: Mapping[str, str]
    ) -> WebhookEvent:
        # PayPal webhooks are verified by POSTing the received event + the
        # transmission headers back to PayPal's verification endpoint.
        payload = json.loads(body)
        verify_body = {
            "auth_algo": headers.get("paypal-auth-algo", ""),
            "cert_url": headers.get("paypal-cert-url", ""),
            "transmission_id": headers.get("paypal-transmission-id", ""),
            "transmission_sig": headers.get("paypal-transmission-sig", ""),
            "transmission_time": headers.get("paypal-transmission-time", ""),
            "webhook_id": self.webhook_id,
            "webhook_event": payload,
        }
        resp = self._client.post(
            "/v1/notifications/verify-webhook-signature",
            headers=self._auth_headers(),
            json=verify_body,
        )
        self._raise_for_status(resp)
        result = resp.json()
        if result.get("verification_status") != "SUCCESS":
            raise WebhookVerificationError(
                f"PayPal webhook verification failed: {result.get('verification_status')}"
            )

        event_name = payload.get("event_type", "")
        event_type = _EVENT_MAP.get(event_name)
        if event_type is None:
            raise WebhookVerificationError(
                f"Unhandled PayPal event type: {event_name}"
            )

        resource = payload.get("resource", {})
        # Renewal events (``PAYMENT.SALE.*``) carry the *sale* as the resource:
        # its own ``id`` is a sale id, while ``billing_agreement_id`` is the
        # subscription id (``I-...``) our local row was created with — so it
        # must win for reconciliation to find the subscription. Subscription
        # lifecycle events (``BILLING.SUBSCRIPTION.*``) are the subscription
        # itself and match by ``id`` either way (subscription resources carry no
        # ``billing_agreement_id``; the subscription id *is* the agreement id).
        external_ref = (
            resource.get("billing_agreement_id")
            or resource.get("id")
            or payload.get("resource_id")
        )
        # One-time orders stamp ``custom_id`` with the charge metadata JSON; the
        # capture/refund resources carry it through, so refund events can match
        # the local unlock even though the resource id (capture id) differs
        # from the order id we stored as the external ref.
        custom_id = resource.get("custom_id")
        try:
            metadata = json.loads(custom_id) if custom_id else {}
        except (TypeError, ValueError):
            metadata = {}
        subscription_status = _normalize_status(
            resource.get("status") or payload.get("resource_status")
        )
        # PayPal events don't expose the billing period. Approximate a 30-day
        # cycle from the resource's create time (the billing date) **only for
        # events that reconcile to an active/trialing subscription** — a failed
        # renewal (``SALE.DENIED``) or a refund must not stamp a future window
        # the cycle never started. The service applies the period only when
        # ``period_end`` is present.
        period_start = period_end = None
        stamps_period = event_type == WebhookEventType.payment_succeeded or (
            event_type
            in (WebhookEventType.subscription_created, WebhookEventType.subscription_updated)
            and subscription_status in ("active", "trialing")
        )
        if stamps_period:
            period_start = _iso_to_dt(resource.get("create_time"))
            if period_start is not None:
                period_end = period_start + timedelta(days=30)
        return WebhookEvent(
            provider=self.name,
            event_type=event_type,
            external_ref=external_ref,
            id=payload.get("id"),
            subscription_status=subscription_status,
            period_start=period_start,
            period_end=period_end,
            metadata=metadata,
            raw=payload,
        )

    def create_one_time_link(self, request: ChargeRequest) -> PaymentLinkResult:
        """Create a hosted PayPal order (approve link) for a one-time payment.

        The customer approves + pays on PayPal's hosted page; the capture
        webhook (``PAYMENT.CAPTURE.COMPLETED``) activates the unlock.
        """
        body = {
            "intent": "CAPTURE",
            "application_context": {
                "brand_name": "Content Creator Engine",
                "user_action": "PAY_NOW",
                "return_url": request.success_url or "https://example.com/success",
                "cancel_url": request.cancel_url or "https://example.com/cancel",
            },
            "purchase_units": [
                {
                    "amount": {
                        "currency_code": request.currency,
                        "value": f"{request.amount_cents / 100:.2f}",
                    },
                    "description": request.description or "",
                    "custom_id": json.dumps(request.metadata),
                }
            ],
        }
        resp = self._client.post(
            "/v2/checkout/orders", headers=self._auth_headers(), json=body
        )
        self._raise_for_status(resp)
        order = resp.json()
        checkout_url = None
        for link in order.get("links", []):
            if link.get("rel") == "approve":
                checkout_url = link.get("href")
        return PaymentLinkResult(
            external_ref=order["id"],
            checkout_url=checkout_url,
            raw=order,
        )

    def charge_one_time(self, request: ChargeRequest) -> ChargeResult:
        body = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "amount": {
                        "currency_code": request.currency,
                        "value": f"{request.amount_cents / 100:.2f}",
                    },
                    "description": request.description or "",
                    "custom_id": json.dumps(request.metadata),
                }
            ],
        }
        resp = self._client.post(
            "/v2/checkout/orders", headers=self._auth_headers(), json=body
        )
        self._raise_for_status(resp)
        order = resp.json()
        return ChargeResult(
            external_ref=order["id"],
            status=order.get("status", "pending"),
            amount_cents=request.amount_cents,
            currency=request.currency,
            raw=order,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._get_token()}"}

    def _get_token(self) -> str:
        if self._access_token:
            return self._access_token
        resp = self._client.post(
            "/v1/oauth2/token",
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials"},
        )
        self._raise_for_status(resp)
        self._access_token = resp.json().get("access_token", "")
        if not self._access_token:
            raise PaymentProviderError("PayPal OAuth did not return an access token")
        return self._access_token

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            detail = resp.text[:500]
            raise PaymentProviderError(
                f"PayPal API error {resp.status_code}: {detail}"
            )

    @classmethod
    def from_settings(cls, settings) -> "PayPalPaymentProvider":
        return cls(
            client_id=settings.PAYPAL_CLIENT_ID,
            client_secret=settings.PAYPAL_CLIENT_SECRET,
            webhook_id=settings.PAYPAL_WEBHOOK_ID,
            environment=getattr(settings, "PAYPAL_ENVIRONMENT", "sandbox"),
            product_id=getattr(settings, "PAYPAL_PRODUCT_ID", "") or None,
        )
