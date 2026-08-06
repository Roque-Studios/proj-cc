"""Provider webhook endpoints.

``POST /webhooks/{provider_name}`` receives provider-signed events (e.g. Stripe
``invoice.paid`` / ``invoice.payment_failed``), verifies the signature with the
named provider's credentials and reconciles them with local subscriptions via
``SubscriptionService``. The provider is resolved by URL name so webhooks are
routed to the right gateway regardless of the active ``PAYMENT_PROVIDER``.
"""

from __future__ import annotations

import json
from typing import Mapping

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..payments import (
    PAYMENT_PROVIDERS,
    PaymentProvider,
    ProviderConfigurationError,
    WebhookVerificationError,
)
from ..services.subscriptions import SubscriptionService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _provider_for(provider_name: str) -> PaymentProvider:
    provider_cls = PAYMENT_PROVIDERS.get(provider_name.strip().lower())
    if provider_cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown payment provider: {provider_name}",
        )
    try:
        return provider_cls.from_settings(settings)
    except ProviderConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Payment provider not configured: {exc}",
        )


@router.post("/{provider_name}")
async def handle_webhook(
    provider_name: str,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Verify a provider webhook and reconcile it with local subscriptions."""
    provider = _provider_for(provider_name)
    service = SubscriptionService(db, provider=provider)
    body = await request.body()
    headers: Mapping[str, str] = {
        k: v for k, v in request.headers.items()
    }
    try:
        event = service.handle_webhook(body, headers)
    except (WebhookVerificationError, json.JSONDecodeError) as exc:
        # Bad signature OR malformed body (e.g. garbage JSON): both are client
        # errors from a public webhook endpoint, so answer 400, never 500.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Webhook verification failed: {exc}",
        )
    return {
        "received": True,
        "provider": event.provider,
        "event_type": event.event_type.value,
        "duplicate": event.duplicate,
    }
