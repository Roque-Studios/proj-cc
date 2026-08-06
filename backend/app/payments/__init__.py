"""Payment gateway abstraction package.

Public API:

- ``PaymentProvider`` — the interface business logic depends on.
- ``get_payment_provider(settings)`` — the configured provider (factory).
- Shared types/exceptions in ``base``.
"""

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
from .factory import PAYMENT_PROVIDERS, get_payment_provider

__all__ = [
    "ChargeRequest",
    "ChargeResult",
    "PAYMENT_PROVIDERS",
    "PaymentProvider",
    "PaymentProviderError",
    "ProviderConfigurationError",
    "SubscriptionIntent",
    "SubscriptionResult",
    "WebhookEvent",
    "WebhookEventType",
    "WebhookVerificationError",
    "get_payment_provider",
]
