"""Bootstrap the PayPal monthly billing plan (sandbox or live).

PayPal subscriptions require a billing plan that already exists at the gateway
(``/v1/billing/subscriptions`` rejects an unknown ``plan_id``). This script
creates the catalog product (if ``PAYPAL_PRODUCT_ID`` is unset) and an ACTIVE
fixed-price monthly plan, then prints the plan id to set as
``SUBSCRIPTION_TIER_PLAN_ID``:

    docker compose exec api python -m app.payments.bootstrap_paypal

Requires ``PAYPAL_CLIENT_ID`` / ``PAYPAL_CLIENT_SECRET`` / ``PAYPAL_WEBHOOK_ID``
and ``PAYPAL_ENVIRONMENT`` (sandbox|live) to be configured; fails fast with a
clear message otherwise. The plan price comes from ``SUBSCRIPTION_TIER_PRICE_CENTS``.
"""

from __future__ import annotations

import argparse
import sys

from ..config import settings
from ..payments import ProviderConfigurationError
from .paypal import PayPalPaymentProvider

PLAN_NAME = "Content Creator Engine — Monthly Tier"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create the PayPal monthly billing plan for the platform tier."
    )
    parser.add_argument(
        "--plan-name",
        default=PLAN_NAME,
        help=f"Billing plan name (default: {PLAN_NAME!r})",
    )
    parser.add_argument(
        "--price-cents",
        type=int,
        default=settings.SUBSCRIPTION_TIER_PRICE_CENTS,
        help="Monthly price in cents (default: SUBSCRIPTION_TIER_PRICE_CENTS)",
    )
    args = parser.parse_args(argv)

    try:
        provider = PayPalPaymentProvider.from_settings(settings)
    except ProviderConfigurationError as exc:
        print(f"[bootstrap] PayPal is not configured: {exc}", file=sys.stderr)
        return 1

    print(
        f"[bootstrap] Creating monthly plan ({args.price_cents} cents) in "
        f"PayPal {provider.environment}…"
    )
    plan = provider.create_plan(
        name=args.plan_name,
        price_cents=args.price_cents,
        currency="usd",
    )
    plan_id = plan["id"]
    status = plan.get("status", "ACTIVE")
    print(f"[bootstrap] Created billing plan {plan_id} (status {status}).")
    if status != "ACTIVE":
        print(
            "[bootstrap] Plan is not ACTIVE — activate it in the PayPal dashboard "
            "before subscribers can use it.",
            file=sys.stderr,
        )
    if provider.product_id:
        print(
            f"[bootstrap] Using product {provider.product_id} "
            "(set PAYPAL_PRODUCT_ID to reuse it)."
        )
    print(f"[bootstrap] Set SUBSCRIPTION_TIER_PLAN_ID={plan_id} and restart the API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
