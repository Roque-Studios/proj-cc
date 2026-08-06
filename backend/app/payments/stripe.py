"""Stripe payment gateway implementation.

Talks to the Stripe REST API over httpx (no SDK dependency). Subscriptions use
hosted Checkout Sessions; cancellation uses the Subscriptions API; webhooks are
verified with Stripe's standard ``t=...,v1=...`` HMAC signature; one-time
charges use Payment Intents.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Mapping

import httpx

# Reject webhook replays older than this (Stripe guidance: a few minutes).
_MAX_SIGNATURE_AGE_SECONDS = 300

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

_API_BASE = "https://api.stripe.com/v1"

# Stripe subscription status -> our normalized status vocabulary.
_STATUS_MAP = {
    "active": "active",
    "trialing": "trialing",
    "incomplete": "incomplete",  # first payment not yet succeeded
    "past_due": "past_due",
    "canceled": "canceled",
    "unpaid": "expired",
    "incomplete_expired": "expired",
    "paused": "expired",
}

# Stripe event type -> normalized webhook event type.
_EVENT_MAP = {
    "customer.subscription.created": WebhookEventType.subscription_created,
    "customer.subscription.updated": WebhookEventType.subscription_updated,
    "customer.subscription.deleted": WebhookEventType.subscription_canceled,
    "checkout.session.completed": WebhookEventType.subscription_created,
    "invoice.paid": WebhookEventType.payment_succeeded,
    "invoice.payment_succeeded": WebhookEventType.payment_succeeded,
    "invoice.payment_failed": WebhookEventType.payment_failed,
    # Fires when a one-time charge (Payment Intent) is refunded — the object is
    # the charge, and its ``payment_intent`` is the ref we stored on the unlock.
    "charge.refunded": WebhookEventType.payment_refunded,
}


def _normalize_status(status: str | None) -> str:
    return _STATUS_MAP.get(status or "", "active")


def _timestamp_to_dt(value) -> datetime | None:
    """Convert a Stripe unix timestamp to an aware UTC datetime (None-safe)."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


class StripePaymentProvider(PaymentProvider):
    name = "stripe"

    def __init__(
        self,
        secret_key: str,
        webhook_secret: str,
        api_base: str = _API_BASE,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not secret_key:
            raise ProviderConfigurationError("Stripe secret key is required")
        if not webhook_secret:
            raise ProviderConfigurationError("Stripe webhook secret is required")
        self.secret_key = secret_key
        self.webhook_secret = webhook_secret
        # ``transport`` is injected in tests (httpx.MockTransport) to simulate
        # the Stripe API; in production it is None and real HTTP is used.
        self._client = httpx.Client(
            base_url=api_base,
            auth=(secret_key, ""),  # Stripe uses the key as the username
            timeout=timeout,
            transport=transport,
        )

    # ------------------------------------------------------------------ #
    # Interface
    # ------------------------------------------------------------------ #

    def create_customer(
        self, email: str, name: str | None = None, metadata: dict | None = None
    ) -> str:
        data = {"email": email}
        if name:
            data["name"] = name
        for key, value in (metadata or {}).items():
            data[f"metadata[{key}]"] = str(value)
        resp = self._client.post("/customers", data=data)
        self._raise_for_status(resp)
        return resp.json()["id"]

    def create_subscription(self, intent: SubscriptionIntent) -> SubscriptionResult:
        data = {
            "mode": "subscription",
            "line_items[0][price]": intent.plan_id,
            "line_items[0][quantity]": "1",
            "success_url": intent.success_url or "https://example.com/success",
            "cancel_url": intent.cancel_url or "https://example.com/cancel",
            "metadata[subscriber_email]": intent.subscriber_email or "",
        }
        # Recurring monthly billing: the Price (plan_id) defines the interval;
        # Checkout collects the payment method (cards/wallets) on the hosted
        # page and stores it on the customer for future renewals.
        if intent.customer_ref:
            data["customer"] = intent.customer_ref
        elif intent.subscriber_email:
            data["customer_email"] = intent.subscriber_email
        for key, value in intent.metadata.items():
            data[f"metadata[{key}]"] = str(value)

        resp = self._client.post("/checkout/sessions", data=data)
        self._raise_for_status(resp)
        session = resp.json()
        # Checkout sessions report "open"/"complete" (payment state, not
        # subscription state); we optimistically default to "active" and let
        # the subscription webhooks reconcile the real status.
        return SubscriptionResult(
            external_ref=session["id"],
            status=_normalize_status(session.get("status")),
            checkout_url=session.get("url"),
            current_period_start=datetime.now(timezone.utc),
            raw=session,
        )

    def cancel_subscription(self, external_ref: str) -> None:
        if external_ref.startswith("cs_"):
            # The row still holds a checkout *session* id (the subscription
            # hasn't been reconciled yet): expire the session instead of
            # deleting a nonexistent subscription.
            resp = self._client.post(f"/checkout/sessions/{external_ref}/expire")
        else:
            resp = self._client.delete(f"/subscriptions/{external_ref}")
        self._raise_for_status(resp)

    def cancel_at_period_end(self, external_ref: str) -> None:
        """Mark the Stripe subscription to not renew (cancel at period end)."""
        resp = self._client.post(
            f"/subscriptions/{external_ref}",
            data={"cancel_at_period_end": "true"},
        )
        self._raise_for_status(resp)

    def verify_webhook(
        self, body: bytes, headers: Mapping[str, str]
    ) -> WebhookEvent:
        signature = headers.get("stripe-signature", "")
        if not signature:
            raise WebhookVerificationError("Missing stripe-signature header")

        # Signature format: t=<timestamp>,v1=<hex>[,v1=<hex>...]
        parts: dict[str, list[str]] = {}
        for piece in signature.split(","):
            key, _, value = piece.partition("=")
            parts.setdefault(key, []).append(value)

        try:
            timestamp = int(parts["t"][0])
        except (KeyError, IndexError, ValueError):
            raise WebhookVerificationError("Malformed stripe-signature header")

        # Replay protection: reject signatures older than a few minutes.
        if abs(int(time.time()) - timestamp) > _MAX_SIGNATURE_AGE_SECONDS:
            raise WebhookVerificationError("Webhook signature is too old")

        expected = self._compute_signature(timestamp, body)
        if not any(hmac.compare_digest(expected, v1) for v1 in parts.get("v1", [])):
            raise WebhookVerificationError("Invalid webhook signature")

        payload = json.loads(body)
        event_type = _EVENT_MAP.get(payload.get("type", ""))
        if event_type is None:
            raise WebhookVerificationError(
                f"Unhandled Stripe event type: {payload.get('type')}"
            )

        obj = payload.get("data", {}).get("object", {})
        metadata = dict(obj.get("metadata", {}))
        # ``checkout.session.completed`` objects carry both the checkout session
        # id (which the local row was created with) and the real subscription id
        # (which every subsequent invoice event references). Surface both so the
        # service can adopt the subscription id on reconciliation.
        if payload.get("type") == "checkout.session.completed":
            metadata["checkout_session_id"] = obj.get("id", "")
        # For ``charge.refunded`` the object is the charge: prefer its
        # ``payment_intent`` (the ref our one-time charges store) over the
        # charge id. This preference is gated to refund events on purpose —
        # real Stripe *invoice* objects also carry a ``payment_intent`` field,
        # and for ``invoice.paid``/``invoice.payment_failed`` the reconcilable
        # ref must stay the subscription id (``pi_...`` would miss the lookup).
        external_ref = obj.get("subscription") or obj.get("id")
        if payload.get("type") == "charge.refunded":
            external_ref = obj.get("payment_intent") or external_ref
        # Invoice events carry the billing period as unix timestamps.
        period_start = _timestamp_to_dt(obj.get("period_start"))
        period_end = _timestamp_to_dt(obj.get("period_end"))
        return WebhookEvent(
            provider=self.name,
            event_type=event_type,
            external_ref=external_ref,
            id=payload.get("id"),
            subscription_status=_normalize_status(obj.get("status")),
            period_start=period_start,
            period_end=period_end,
            metadata=metadata,
            raw=payload,
        )

    def charge_one_time(self, request: ChargeRequest) -> ChargeResult:
        data = {
            "amount": str(request.amount_cents),
            "currency": request.currency,
            "description": request.description or "",
            "confirm": "true",
            "automatic_payment_methods[enabled]": "true",
        }
        for key, value in request.metadata.items():
            data[f"metadata[{key}]"] = str(value)
        resp = self._client.post("/payment_intents", data=data)
        self._raise_for_status(resp)
        intent = resp.json()
        return ChargeResult(
            external_ref=intent["id"],
            status=intent.get("status", "pending"),
            amount_cents=request.amount_cents,
            currency=request.currency,
            raw=intent,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _compute_signature(self, timestamp: int, body: bytes) -> str:
        signed_payload = f"{timestamp}.".encode() + body
        return hmac.new(
            self.webhook_secret.encode(), signed_payload, hashlib.sha256
        ).hexdigest()

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            detail = resp.text[:500]
            raise PaymentProviderError(
                f"Stripe API error {resp.status_code}: {detail}"
            )

    @classmethod
    def from_settings(cls, settings) -> "StripePaymentProvider":
        return cls(
            secret_key=settings.STRIPE_SECRET_KEY,
            webhook_secret=settings.STRIPE_WEBHOOK_SECRET,
            api_base=getattr(settings, "STRIPE_API_BASE", _API_BASE),
        )
