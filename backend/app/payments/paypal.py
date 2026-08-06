"""PayPal payment gateway implementation.

Talks to the PayPal REST API over httpx (no SDK dependency). Uses OAuth2 client
credentials for auth, the Billing Subscriptions API for subscriptions and
cancellation, the webhook signature verification endpoint for webhooks, and
Orders v2 for one-time charges.
"""

from __future__ import annotations

import json
from typing import Mapping

import httpx

from .base import (
    ChargeRequest,
    ChargeResult,
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
}


def _normalize_status(status: str | None) -> str:
    return _STATUS_MAP.get(status or "", "active")


class PayPalPaymentProvider(PaymentProvider):
    name = "paypal"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        webhook_id: str,
        environment: str = "sandbox",
        timeout: float = 30.0,
    ) -> None:
        if not client_id or not client_secret:
            raise ProviderConfigurationError(
                "PayPal client id and secret are required"
            )
        if not webhook_id:
            raise ProviderConfigurationError("PayPal webhook id is required")
        base = _LIVE_BASE if environment == "live" else _SANDBOX_BASE
        self.client_id = client_id
        self.client_secret = client_secret
        self.webhook_id = webhook_id
        self._client = httpx.Client(base_url=base, timeout=timeout)
        self._access_token: str | None = None

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
        return WebhookEvent(
            provider=self.name,
            event_type=event_type,
            external_ref=resource.get("id") or payload.get("resource_id"),
            id=payload.get("id"),
            subscription_status=_normalize_status(
                resource.get("status") or payload.get("resource_status")
            ),
            metadata={},
            raw=payload,
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
        )
