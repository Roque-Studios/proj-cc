"""Payment provider registry & factory.

Registering a new gateway is a one-line change here (``PAYMENT_PROVIDERS``) plus
a provider class — the subscription business logic never changes. The active
gateway is selected by the ``PAYMENT_PROVIDER`` setting (``mock`` default).
"""

from __future__ import annotations

from .base import PaymentProvider, ProviderConfigurationError
from .mock import MockPaymentProvider
from .paypal import PayPalPaymentProvider
from .stripe import StripePaymentProvider

PAYMENT_PROVIDERS: dict[str, type[PaymentProvider]] = {
    "mock": MockPaymentProvider,
    "stripe": StripePaymentProvider,
    "paypal": PayPalPaymentProvider,
}


def get_payment_provider(settings) -> PaymentProvider:
    """Build the configured payment provider.

    Raises ``ProviderConfigurationError`` (fail fast, clear message) if the
    provider is unknown or its credentials are missing.
    """
    name = (settings.PAYMENT_PROVIDER or "mock").strip().lower()
    provider_cls = PAYMENT_PROVIDERS.get(name)
    if provider_cls is None:
        raise ProviderConfigurationError(
            f"Unknown payment provider '{name}'. "
            f"Available: {', '.join(sorted(PAYMENT_PROVIDERS))}"
        )
    return provider_cls.from_settings(settings)
