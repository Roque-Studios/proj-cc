"""Provider webhook endpoints.

``POST /webhooks/{provider_name}`` receives provider-signed events (e.g. Stripe
``invoice.paid`` / ``invoice.payment_failed`` / ``charge.refunded``), verifies
the signature with the named provider's credentials **once**, then dispatches
by normalized type: subscription lifecycle events reconcile local subscriptions
via ``SubscriptionService``; ``payment.refunded`` events revoke the matching
one-time ``PaidUnlock`` via ``BroadcastService`` (separate from the monthly
subscription charge). The provider is resolved by URL name so webhooks are
routed to the right gateway regardless of the active ``PAYMENT_PROVIDER``.

Credentials are **per-creator** (see ``CreatorGatewayConfig``), so signature
verification tries every registered credential set for the gateway — the
platform env config first, then each creator's stored config. An event only
passes when it matches one of them; a forged event fails all and gets a 400.
"""

from __future__ import annotations

import json
from typing import Mapping

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import CreatorGatewayConfig
from ..payments import (
    PAYMENT_PROVIDERS,
    PaymentProvider,
    ProviderConfigurationError,
    WebhookEventType,
    WebhookVerificationError,
)
from ..payments.factory import build_provider_from_config
from ..services.broadcasts import BroadcastService
from ..services.subscriptions import SubscriptionService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _candidate_providers(db: Session, provider_name: str) -> list[PaymentProvider]:
    """Every registered credential set for a gateway, platform env first.

    The platform env config is tried first (keeps env-driven dev/test setups
    working), then each creator's stored per-gateway config. Providers whose
    credentials are incomplete are skipped.
    """
    provider_cls = PAYMENT_PROVIDERS.get(provider_name.strip().lower())
    if provider_cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown payment provider: {provider_name}",
        )
    candidates: list[PaymentProvider] = []
    try:
        candidates.append(provider_cls.from_settings(settings))
    except ProviderConfigurationError:
        pass  # platform env gateway not configured — per-creator configs may be
    rows = db.scalars(
        select(CreatorGatewayConfig).where(
            CreatorGatewayConfig.gateway == provider_name.strip().lower()
        )
    ).all()
    for row in rows:
        try:
            candidates.append(build_provider_from_config(row.gateway, row.config))
        except ProviderConfigurationError:
            continue  # an incomplete stored config can't verify anything
    return candidates


@router.post("/{provider_name}")
async def handle_webhook(
    provider_name: str,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Verify a provider webhook once, then reconcile it with the right flow.

    ``payment.refunded`` events (a refunded one-time broadcast unlock) go to
    ``BroadcastService.handle_refunded``; every other verified event goes to
    ``SubscriptionService.handle_webhook`` with the pre-verified event so the
    signature is checked exactly once per delivery.
    """
    candidates = _candidate_providers(db, provider_name)
    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Payment provider '{provider_name}' is not configured",
        )

    body = await request.body()
    headers: Mapping[str, str] = {
        k: v for k, v in request.headers.items()
    }
    provider: PaymentProvider | None = None
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            event = candidate.verify_webhook(body, headers)
            provider = candidate
            break
        except (WebhookVerificationError, json.JSONDecodeError) as exc:
            # Keep trying the other registered credential sets; a malformed
            # body fails every candidate the same way, so only the last error
            # surfaces.
            last_error = exc
    if provider is None:
        # Bad signature OR malformed body (e.g. garbage JSON): both are client
        # errors from a public webhook endpoint, so answer 400, never 500.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Webhook verification failed: {last_error}",
        )

    if event.event_type == WebhookEventType.payment_refunded:
        # One-time unlock refund: separate from the subscription lifecycle.
        event = BroadcastService(db, provider=provider).handle_refunded(event)
    else:
        event = SubscriptionService(db, provider=provider).handle_webhook(
            body, headers, event=event
        )
    return {
        "received": True,
        "provider": event.provider,
        "event_type": event.event_type.value,
        "duplicate": event.duplicate,
    }
