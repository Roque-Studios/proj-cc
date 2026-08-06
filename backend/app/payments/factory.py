"""Payment provider registry & factory.

Registering a new gateway is a one-line change here (``PAYMENT_PROVIDERS``) plus
a provider class — the subscription business logic never changes. The active
gateway is selected by the ``PAYMENT_PROVIDER`` setting (``mock`` default).

Creators can also configure gateways **per-creator** (see ``app.gateways`` and
the ``CreatorGatewayConfig`` model): ``build_provider_from_config`` builds a
provider for one creator's stored config, merging those fields over the
platform settings (tier price, plan defaults, URLs) so each provider's
``from_settings`` validation still applies.
"""

from __future__ import annotations

from types import SimpleNamespace

from ..config import settings
from .base import PaymentProvider, ProviderConfigurationError
from .mock import MockPaymentProvider
from .paypal import PayPalPaymentProvider
from .stripe import StripePaymentProvider
from .wompi import WompiPaymentProvider

PAYMENT_PROVIDERS: dict[str, type[PaymentProvider]] = {
    "mock": MockPaymentProvider,
    "stripe": StripePaymentProvider,
    "paypal": PayPalPaymentProvider,
    "wompi": WompiPaymentProvider,
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


# Settings attributes that must be ints when merged from a per-creator config
# (stored values are strings from the API form). ``WOMPI_DIA_DE_PAGO`` lands in
# ``WompiPaymentProvider`` which compares ``1 <= dia_de_pago <= 31`` — a string
# would raise TypeError there instead of a clean config error.
_NUMERIC_SETTING_ATTRS = {"WOMPI_DIA_DE_PAGO"}

# Per-creator config field -> settings attribute each provider's ``from_settings``
# reads. The per-creator config keys (see ``app.gateways.GatewaySpec``) are
# mapped onto the platform settings vocabulary so the providers need no changes.
_CONFIG_FIELD_TO_SETTING: dict[tuple[str, str], str] = {
    ("stripe", "secret_key"): "STRIPE_SECRET_KEY",
    ("stripe", "webhook_secret"): "STRIPE_WEBHOOK_SECRET",
    ("stripe", "api_base"): "STRIPE_API_BASE",
    ("paypal", "client_id"): "PAYPAL_CLIENT_ID",
    ("paypal", "client_secret"): "PAYPAL_CLIENT_SECRET",
    ("paypal", "webhook_id"): "PAYPAL_WEBHOOK_ID",
    ("paypal", "environment"): "PAYPAL_ENVIRONMENT",
    ("paypal", "product_id"): "PAYPAL_PRODUCT_ID",
    ("wompi", "client_id"): "WOMPI_CLIENT_ID",
    ("wompi", "client_secret"): "WOMPI_CLIENT_SECRET",
    ("wompi", "environment"): "WOMPI_ENVIRONMENT",
    ("wompi", "api_base_url"): "WOMPI_API_BASE_URL",
    ("wompi", "token_url"): "WOMPI_TOKEN_URL",
    ("wompi", "dia_de_pago"): "WOMPI_DIA_DE_PAGO",
    ("wompi", "redirect_url"): "WOMPI_3DS_REDIRECT_URL",
}


def build_provider_from_config(gateway: str, config: dict) -> PaymentProvider:
    """Build a provider from a creator's stored per-gateway config.

    The stored fields are merged over the platform settings (so tier pricing,
    plan defaults and API URLs still resolve), then the provider's
    ``from_settings`` runs its normal credential validation. Raises
    ``ProviderConfigurationError`` for unknown gateways or missing
    credentials.
    """
    provider_cls = PAYMENT_PROVIDERS.get(gateway.strip().lower())
    if provider_cls is None:
        raise ProviderConfigurationError(
            f"Unknown payment provider '{gateway}'. "
            f"Available: {', '.join(sorted(PAYMENT_PROVIDERS))}"
        )
    overrides = {
        _CONFIG_FIELD_TO_SETTING[(gateway, field)]: value
        for (gw, field), attr in _CONFIG_FIELD_TO_SETTING.items()
        if gw == gateway
        for value in [config.get(field)]
        if value not in (None, "")
    }
    # Coerce numeric settings back to int (config values arrive as strings).
    for attr in _NUMERIC_SETTING_ATTRS & overrides.keys():
        try:
            overrides[attr] = int(overrides[attr])
        except (TypeError, ValueError):
            # Field validation already bounds this to 1-31; a corrupted row
            # surfaces as the provider's own ProviderConfigurationError below.
            pass
    merged = SimpleNamespace(**{**settings.model_dump(), **overrides})
    return provider_cls.from_settings(merged)


def resolve_plan_id(gateway: str, config: dict) -> str:
    """The billing plan id for a per-creator gateway config.

    A creator may pin their own plan (e.g. a PayPal ``P-...`` billing plan they
    bootstrapped); otherwise the platform's single monthly tier applies.
    """
    plan = config.get("plan_id")
    if plan is not None and str(plan).strip():
        return str(plan).strip()
    return settings.SUBSCRIPTION_TIER_PLAN_ID
