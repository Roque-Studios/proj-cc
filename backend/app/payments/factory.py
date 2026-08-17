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
from ..gateways import PLAN_ID_HINTS, PLAN_ID_PREFIXES
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
    ("wompi", "webhook_url"): "WOMPI_WEBHOOK_URL",
    ("wompi", "environment"): "WOMPI_ENVIRONMENT",
    ("wompi", "api_base_url"): "WOMPI_API_BASE_URL",
    ("wompi", "token_url"): "WOMPI_TOKEN_URL",
    ("wompi", "redirect_url"): "WOMPI_REDIRECT_URL",
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
    merged = SimpleNamespace(**{**settings.model_dump(), **overrides})
    return provider_cls.from_settings(merged)


# The required plan-id shape per gateway (the value sent as the billing plan
# must look like the gateway's own plan id) and the operator-facing fix hint
# live in ``app.gateways`` — the registry also validates them at save time, so
# the shape rules have a single source of truth.


def _validate_plan_id_shape(gateway: str, plan_id: str) -> None:
    """Reject a plan id that can't belong to the gateway (fail fast, clear msg).

    Raises ``ProviderConfigurationError`` so the caller surfaces an actionable
    message instead of letting a garbage plan id reach the gateway and bounce
    back as a cryptic provider 400 (e.g. PayPal rejecting the Stripe
    placeholder default ``price_monthly_tier``).
    """
    prefix = PLAN_ID_PREFIXES.get(gateway)
    if prefix is None or plan_id.startswith(prefix):
        return
    raise ProviderConfigurationError(
        f"{gateway.title()} subscriptions require a billing plan id that starts "
        f"with '{prefix}' (got '{plan_id}'). {PLAN_ID_HINTS[gateway]}"
    )


def resolve_plan_id(gateway: str, config: dict) -> str:
    """The billing plan id for a per-creator gateway config.

    A creator may pin their own plan (e.g. a PayPal ``P-...`` billing plan they
    bootstrapped); otherwise the platform's single monthly tier applies. The
    resolved id is validated against the gateway's required id shape — a
    misconfigured plan fails fast with a clear ``ProviderConfigurationError``
    rather than a cryptic rejection from the gateway.
    """
    plan = config.get("plan_id")
    if plan is not None and str(plan).strip():
        plan_id = str(plan).strip()
    else:
        plan_id = settings.SUBSCRIPTION_TIER_PLAN_ID
    _validate_plan_id_shape(gateway, plan_id)
    return plan_id
