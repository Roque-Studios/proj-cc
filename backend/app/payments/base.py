"""Payment gateway abstraction layer.

``PaymentProvider`` is the single interface the subscription business logic
depends on. Each gateway (Stripe, PayPal, mock) is a separate implementation;
switching or adding a gateway only requires registering it in the factory —
never touching ``services.subscriptions`` or any other business logic.

The interface is deliberately gateway-agnostic: it speaks in neutral terms
(plan id, external ref, normalized status strings) instead of provider-specific
objects, so callers never leak Stripe/PayPal vocabulary.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping


class PaymentProviderError(Exception):
    """Base error for all payment provider failures."""


class ProviderConfigurationError(PaymentProviderError):
    """Raised when a provider is selected but its credentials are missing/invalid."""


class WebhookVerificationError(PaymentProviderError):
    """Raised when a webhook signature cannot be verified."""


class WebhookEventType(enum.Enum):
    """Normalized webhook event types shared across all providers."""

    subscription_created = "subscription.created"
    subscription_updated = "subscription.updated"
    subscription_canceled = "subscription.canceled"
    payment_succeeded = "payment.succeeded"
    payment_failed = "payment.failed"
    payment_refunded = "payment.refunded"


@dataclass
class SubscriptionIntent:
    """Request to create a subscription with a provider."""

    plan_id: str
    subscriber_email: str | None = None
    subscriber_name: str | None = None
    success_url: str | None = None
    cancel_url: str | None = None
    # Gateway customer reference (created via ``PaymentProvider.create_customer``).
    customer_ref: str | None = None
    # The **actual monthly price** in cents the subscriber agreed to (the
    # creator's own tier price). Providers that charge a direct amount (Wompi
    # payment links) use this; plan-based providers (Stripe/PayPal) keep their
    # gateway plan id and ignore it. ``None`` = the provider's configured
    # default price.
    amount_cents: int | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class SubscriptionResult:
    """Provider response for a created subscription.

    ``status`` is already normalized to our vocabulary (e.g. ``active``,
    ``trialing``); ``checkout_url`` is the hosted checkout a client redirects to.
    """

    external_ref: str
    status: str
    checkout_url: str | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class ChargeRequest:
    """Request to charge a one-time amount."""

    amount_cents: int
    currency: str = "usd"
    description: str | None = None
    metadata: dict = field(default_factory=dict)
    # A tokenized payment method (e.g. a Wompi card token from client-side
    # tokenization). Optional — gateways with hosted checkout (Stripe/PayPal
    # one-time links) don't need it; Wompi's tokenized charge does.
    payment_method_token: str | None = None
    # Hosted-checkout return urls (one-time payment links). After the customer
    # pays on the gateway's page they are redirected back to ``success_url``;
    # ``cancel_url`` is where they land if they back out.
    success_url: str | None = None
    cancel_url: str | None = None


@dataclass
class ChargeResult:
    external_ref: str
    status: str  # succeeded | pending | failed
    amount_cents: int
    currency: str
    raw: dict = field(default_factory=dict)


@dataclass
class PaymentLinkResult:
    """A hosted one-time payment link (the unlock equivalent of a checkout).

    ``external_ref`` is the gateway resource id to reconcile the payment
    webhook against (the Wompi link id / Stripe checkout session id / PayPal
    order id); ``checkout_url`` is where the customer pays on the hosted page.
    """

    external_ref: str
    checkout_url: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class WebhookEvent:
    """A verified webhook event, normalized to our vocabulary.

    ``external_ref`` is the provider subscription id (when the event concerns a
    subscription) so the business logic can reconcile it with our rows.
    ``subscription_status`` is a normalized status string (e.g. ``active``,
    ``canceled``) when the event carries one, else ``None``.
    ``period_start`` / ``period_end`` are the billing period the event refers to
    (e.g. from Stripe invoice events) when the provider includes them.
    """

    provider: str
    event_type: WebhookEventType
    external_ref: str | None
    # The provider's unique event id (e.g. Stripe ``evt_...``). Used to make
    # webhook processing idempotent: a re-delivered event (provider retry) with
    # the same (provider, id) is recognized and skipped.
    id: str | None = None
    # Set by the service when this event was already processed (duplicate
    # delivery) — the router surfaces it so providers get a 200 and stop retrying.
    duplicate: bool = False
    subscription_status: str | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    # The payer's email when the event carries it. Used as a reconciliation
    # fallback for gateways whose events don't reference our stored ref
    # directly (e.g. Wompi recurring-link charges).
    customer_email: str | None = None
    # True when the event concerns a **recurring** (subscription) charge — the
    # email fallback in the subscription service is gated on this flag so a
    # provider's one-time purchase events (same email, no subscription ref)
    # can never be misreconciled against a subscription row (and never record
    # a spurious monthly payment in the revenue ledger).
    recurring: bool = False
    metadata: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


class PaymentProvider(ABC):
    """Interface every payment gateway must implement."""

    name: str = "base"

    @abstractmethod
    def create_customer(
        self, email: str, name: str | None = None, metadata: dict | None = None
    ) -> str:
        """Create (or return the ref of) a customer at the gateway.

        Returns the gateway customer reference (e.g. Stripe's ``cus_...``),
        which is then cached on the user and passed back in future intents.
        """

    @abstractmethod
    def create_subscription(self, intent: SubscriptionIntent) -> SubscriptionResult:
        """Create a subscription and return the hosted checkout details."""

    @abstractmethod
    def cancel_subscription(self, external_ref: str) -> None:
        """Cancel an active subscription immediately at the provider."""

    @abstractmethod
    def cancel_at_period_end(self, external_ref: str) -> None:
        """Tell the provider not to renew at the end of the current period.

        The subscription remains active (access persists) until the period
        ends; our scheduled task then flips it to canceled. Providers without
        this concept may implement as a best-effort no-op.
        """

    @abstractmethod
    def verify_webhook(self, body: bytes, headers: Mapping[str, str]) -> WebhookEvent:
        """Verify a webhook signature and return the normalized event.

        Raises ``WebhookVerificationError`` when the signature is invalid.
        """

    @abstractmethod
    def charge_one_time(self, request: ChargeRequest) -> ChargeResult:
        """Charge a one-time amount."""

    @abstractmethod
    def create_one_time_link(self, request: ChargeRequest) -> PaymentLinkResult:
        """Create a hosted one-time payment link (redirect checkout).

        The customer pays on the gateway's page (card/wallet collected there)
        and the outcome arrives by webhook — the same hosted pattern as
        subscriptions, so no client-side card tokenization is ever needed.
        """

    @classmethod
    def from_settings(cls, settings) -> "PaymentProvider":
        """Build a provider from app settings, validating credentials.

        Must raise ``ProviderConfigurationError`` (fail fast) when a required
        credential is missing.
        """
        raise ProviderConfigurationError(
            f"{cls.__name__} does not define from_settings()"
        )
