"""Per-creator gateway config data access shared by the settings/checkout flows."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..gateways import CREATOR_GATEWAY_ORDER, is_config_complete
from ..models import CreatorGatewayConfig
from ..payments import PaymentProvider, get_payment_provider
from ..payments.factory import build_provider_from_config


def gateway_rows(db: Session, creator_id: int) -> dict[str, CreatorGatewayConfig]:
    """All gateway config rows for a creator, keyed by gateway name."""
    rows = db.scalars(
        select(CreatorGatewayConfig).where(
            CreatorGatewayConfig.creator_id == creator_id
        )
    ).all()
    return {row.gateway: row for row in rows}


def get_gateway_row(
    db: Session, creator_id: int, gateway: str
) -> CreatorGatewayConfig | None:
    """A creator's config row for one gateway, or None."""
    return db.scalar(
        select(CreatorGatewayConfig).where(
            CreatorGatewayConfig.creator_id == creator_id,
            CreatorGatewayConfig.gateway == gateway,
        )
    )


def enabled_configured_gateways(
    db: Session, creator_id: int
) -> list[tuple[str, CreatorGatewayConfig]]:
    """The creator's enabled gateways with complete configs, in registry order.

    This is the exact set a subscriber may pay with (the checkout list), and
    the set ``POST /subscribe`` resolves a gateway from.
    """
    rows = gateway_rows(db, creator_id)
    return [
        (gateway, row)
        for gateway in CREATOR_GATEWAY_ORDER
        if (row := rows.get(gateway))
        and row.enabled
        and is_config_complete(gateway, row.config)
    ]


def resolve_unlock_provider(db: Session, creator_id: int) -> PaymentProvider:
    """The provider used to create a creator's one-time unlock checkouts.

    Prefers the creator's **single** enabled+configured gateway (the same
    account + webhook secret the subscription checkout uses, so payment
    events reconcile); when the creator has none (or several — ambiguous),
    falls back to the platform env provider so the zero-config mock/dev path
    keeps working.
    """
    enabled = enabled_configured_gateways(db, creator_id)
    if len(enabled) == 1:
        gateway, row = enabled[0]
        return build_provider_from_config(gateway, row.config)
    return get_payment_provider(settings)
