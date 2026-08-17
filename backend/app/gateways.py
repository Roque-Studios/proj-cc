"""Creator-configurable payment gateway registry + config validation.

Creators configure gateways strictly per-creator (no platform env fallback for
checkout): each gateway here declares the credential fields its provider needs
(``required`` fields must be non-empty to enable the gateway), which fields are
secrets (never echoed back by read paths), and any constrained values
(environments, payment day). Enabling a gateway with an incomplete config is
rejected with a clear list of the missing fields.

The mock gateway has no credentials — it exists so dev/test setups can enable a
zero-config gateway.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

# The billing-plan id shape each gateway expects (PayPal ``P-...``, Stripe
# ``price_...``): a plan id that can't belong to the gateway is rejected at
# save time (and checkout) instead of bouncing off the gateway with a cryptic
# schema error (e.g. PayPal's ``INVALID_REQUEST`` on ``/plan_id``). Gateways
# absent here never send a plan id (Wompi payment links price by amount; mock
# accepts anything).
PLAN_ID_PREFIXES: dict[str, str] = {
    "paypal": "P-",
    "stripe": "price_",
}

# Operator-facing fix hint for a plan id that fails the gateway's shape check
# (surfaced in the settings-form error and in server logs — never to
# subscribers).
PLAN_ID_HINTS: dict[str, str] = {
    "paypal": (
        "Create the monthly billing plan with the 'Create billing plan' button "
        "in the payment-gateway settings page — it saves the P-... id to your "
        "PayPal config automatically (the platform default comes from "
        "`python -m app.payments.bootstrap_paypal`)."
    ),
    "stripe": (
        "Create a recurring price in the Stripe dashboard and enter its "
        "price_... id here."
    ),
}


@dataclass(frozen=True)
class GatewayField:
    """One credential/config field of a gateway's settings form."""

    name: str
    label: str
    required: bool = False
    # Secret fields (api keys, webhook secrets) are never returned by API read
    # paths — only a per-field ``configured`` boolean is.
    secret: bool = True
    placeholder: str = ""
    # Allowed values (e.g. sandbox/live). Empty = free-form text.
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class GatewaySpec:
    """Static description of one creator-configurable gateway."""

    name: str
    label: str
    description: str
    fields: tuple[GatewayField, ...] = field(default_factory=tuple)


GATEWAYS: dict[str, GatewaySpec] = {
    "stripe": GatewaySpec(
        name="stripe",
        label="Stripe",
        description=(
            "Accept cards and wallets through your Stripe account. You'll need "
            "a secret key and the webhook signing secret from your Stripe "
            "dashboard."
        ),
        fields=(
            GatewayField("secret_key", "Secret key", required=True, placeholder="sk_live_..."),
            GatewayField("webhook_secret", "Webhook secret", required=True, placeholder="whsec_..."),
            GatewayField("api_base", "API base URL", placeholder="https://api.stripe.com/v1"),
        ),
    ),
    "paypal": GatewaySpec(
        name="paypal",
        label="PayPal",
        description=(
            "Recurring subscriptions via PayPal Billing (hosted approval). Use "
            "your REST app credentials and the webhook id from the developer "
            "dashboard; the monthly billing plan is created from this page."
        ),
        fields=(
            GatewayField("client_id", "Client ID", required=True),
            GatewayField("client_secret", "Client secret", required=True),
            GatewayField("webhook_id", "Webhook ID", required=True),
            GatewayField(
                "environment",
                "Environment",
                options=("sandbox", "live"),
                secret=False,
            ),
            GatewayField(
                "plan_id",
                "Billing plan ID",
                secret=False,
                placeholder="P-... (saved automatically by 'Create billing plan')",
            ),
        ),
    ),
    "wompi": GatewaySpec(
        name="wompi",
        label="Wompi",
        description=(
            "Cards via Wompi. Enter your App ID (client id), API Secret "
            "(client secret) and the Webhook URL Wompi notifies after every "
            "successful payment — the backend's /api/webhooks/wompi endpoint."
        ),
        fields=(
            GatewayField("client_id", "WOMPI_CLIENT_ID (App ID)", required=True),
            GatewayField("client_secret", "WOMPI_CLIENT_SECRET (API Secret)", required=True),
            GatewayField(
                "webhook_url",
                "Webhook URL",
                required=True,
                secret=False,
                placeholder="https://your-domain.com/api/webhooks/wompi",
            ),
            GatewayField(
                "environment",
                "Environment",
                options=("sandbox", "production"),
                secret=False,
            ),
            GatewayField(
                "redirect_url",
                "Redirect URL after payment (optional)",
                secret=False,
            ),
        ),
    ),
    "mock": GatewaySpec(
        name="mock",
        label="Mock (development)",
        description=(
            "Zero-configuration gateway for development/testing — checkout "
            "links are fake and no real money moves."
        ),
    ),
}

# Order used for settings listings and checkout.
CREATOR_GATEWAY_ORDER: tuple[str, ...] = ("stripe", "paypal", "wompi", "mock")

# Gateways shown in the creator settings UI (mock is a backend-only dev tool).
UI_GATEWAY_ORDER: tuple[str, ...] = ("stripe", "paypal", "wompi")


def spec_for(gateway: str) -> GatewaySpec:
    """Return the spec for a gateway name, raising KeyError for unknown ones."""
    return GATEWAYS[gateway]


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def merge_config(
    gateway: str,
    submitted: dict,
    existing: dict | None = None,
) -> dict:
    """Merge a submitted config over the stored one.

    Empty/whitespace values and absent fields keep the existing stored value
    (so a UI that never echoes secrets back can still round-trip updates
    without wiping them). Returns the merged dict.
    """
    merged = dict(existing or {})
    spec = spec_for(gateway)
    for field in spec.fields:
        raw = submitted.get(field.name)
        if raw is None:
            continue
        value = str(raw).strip()
        if value == "":
            continue  # keep existing (or leave unset)
        merged[field.name] = value
    return merged


def validate_config_values(gateway: str, config: dict) -> None:
    """Raise ValueError for invalid field values (environments, day of month).

    Does NOT check completeness — only that the values present are valid.
    """
    spec = spec_for(gateway)
    for field in spec.fields:
        value = config.get(field.name)
        if value is None:
            continue
        if field.options and str(value).strip() not in field.options:
            raise ValueError(
                f"{field.label} must be one of: {', '.join(field.options)}"
            )
        # A stored plan id must look like the gateway's own (PayPal ``P-...``,
        # Stripe ``price_...``) or checkout would fail at the gateway with a
        # cryptic schema error — reject it at save time with a clear message so
        # a bad id can never sneak past the settings page to checkout.
        if field.name == "plan_id" and str(value).strip():
            prefix = PLAN_ID_PREFIXES.get(gateway)
            if prefix is not None and not str(value).strip().startswith(prefix):
                raise ValueError(
                    f"{field.label} must start with '{prefix}' for "
                    f"{GATEWAYS[gateway].label} subscriptions (got '{value}'). "
                    f"{PLAN_ID_HINTS[gateway]}"
                )


def missing_fields(gateway: str, config: dict) -> list[str]:
    """Names of required fields with no configured value."""
    spec = spec_for(gateway)
    return [
        field.label
        for field in spec.fields
        if field.required and not str(config.get(field.name, "")).strip()
    ]


def is_config_complete(gateway: str, config: dict) -> bool:
    """True when every required field has a non-empty value."""
    return not missing_fields(gateway, config)


def validate_enable(gateway: str, config: dict) -> None:
    """Validate a config that is about to be enabled.

    Raises ValueError with the human-readable problem (invalid values or the
    list of missing required fields) — the router maps it to a 400.
    """
    validate_config_values(gateway, config)
    missing = missing_fields(gateway, config)
    if missing:
        raise ValueError(
            f"Cannot enable {GATEWAYS[gateway].label}: missing required "
            f"field(s): {', '.join(missing)}"
        )
