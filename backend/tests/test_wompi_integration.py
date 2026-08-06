"""Wompi (El Salvador) integration tests.

The real Wompi SV API is simulated with ``httpx.MockTransport`` injected into
``pywompi.WompiClient(http_client=...)``, so the provider's real request
building, OAuth2 client-credentials auth, and webhook-signature validation
(``wompi_hash`` = HMAC-SHA256 of the raw body with the API secret) run against
a faithful fake of the Swagger (recurring links, tokenized transactions, 3DS).
Covers the acceptance criteria:

- subscribing creates an ``incomplete`` local row with the hosted recurring
  link as checkout url (``external_ref`` = link id);
- a signature-valid ``APROBADA`` webhook activates the subscription (matched
  by the payer email, then adopted by ref for renewals) — the *sandbox
  transaction completes and activates the Subscription* acceptance;
- forged signatures are rejected before anything is processed;
- renewals reconcile by the adopted suscripcion ref; failures move to
  ``past_due``;
- one-time (tokenized, no 3DS) and 3DS redirect charges work.

A real-sandbox test (``test_wompi_sandbox_subscribe_flow``) runs only when
``WOMPI_CLIENT_ID`` / ``WOMPI_CLIENT_SECRET`` are provided **and**
``RUN_WOMPI_SANDBOX=1``; it creates a real recurring link and asserts the
local row, leaving the hosted payment + webhook activation to the simulated
tests above.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
from pywompi import compute_signature
from sqlalchemy import select

from app.config import settings
from app.models import ProcessedWebhookEvent, Subscription, SubscriptionStatus, User, UserRole
from app.payments import (
    ChargeRequest,
    PaymentProviderError,
    WebhookVerificationError,
)
from app.payments.wompi import WompiPaymentProvider
from app.services.subscriptions import SubscriptionService

WOMPI_CLIENT_ID = "app_wompi_test"
WOMPI_CLIENT_SECRET = "wompi_api_secret_test"


# --------------------------------------------------------------------------- #
# A fake Wompi SV sandbox (httpx.MockTransport handler)
# --------------------------------------------------------------------------- #

class FakeWompiAPI:
    """In-memory Wompi SV: OAuth, recurring links, tokenized/3DS transactions."""

    def __init__(self) -> None:
        self.links: dict[str, dict] = {}
        self.transactions: dict[str, dict] = {}
        self.approve_next_charge = True
        self._seq = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/connect/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "fake_wompi_token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        if request.method == "POST" and path == "/EnlacePagoRecurrente":
            return self._create_recurring_link(request)
        if request.method == "POST" and path.startswith("/EnlacePagoRecurrente/"):
            return self._disable_recurring_link(request)
        if request.method == "POST" and path == "/TransaccionCompra/TokenizadaSin3Ds":
            return self._tokenized_charge(request)
        if request.method == "POST" and path == "/TransaccionCompra/3Ds":
            return self._charge_3ds(request)
        return httpx.Response(404, text='{"mensaje": "not found"}')

    def _create_recurring_link(self, request: httpx.Request) -> httpx.Response:
        self._seq += 1
        body = json.loads(request.content)
        link_id = f"ENLACE-{self._seq}"
        link = {
            "idEnlace": link_id,
            "nombre": body.get("nombre"),
            "monto": body.get("monto"),
            "diaDePago": body.get("diaDePago"),
            "descripcionProducto": body.get("descripcionProducto"),
            "estaProductivo": True,
            "urlEnlace": f"https://wompi.sv/pagar/{link_id}",
            "urlEnlaceLargo": f"https://wompi.sv/pagar/largo/{link_id}",
        }
        self.links[link_id] = link
        return httpx.Response(200, json=link)

    def _disable_recurring_link(self, request: httpx.Request) -> httpx.Response:
        link_id = request.url.path.split("/")[-1]
        if link_id not in self.links:
            return httpx.Response(404, text='{"mensaje": "not found"}')
        self.links[link_id]["estaProductivo"] = False
        return httpx.Response(204)

    def _tokenized_charge(self, request: httpx.Request) -> httpx.Response:
        self._seq += 1
        body = json.loads(request.content)
        tx_id = f"SV-{self._seq}"
        tx = {
            "idTransaccion": tx_id,
            "esReal": True,
            "esAprobada": self.approve_next_charge,
            "codigoAutorizacion": "AUTH-0001" if self.approve_next_charge else None,
            "mensaje": "APROBADA" if self.approve_next_charge else "RECHAZADA",
            "monto": body.get("monto"),
        }
        self.transactions[tx_id] = tx
        return httpx.Response(200, json=tx)

    def _charge_3ds(self, request: httpx.Request) -> httpx.Response:
        self._seq += 1
        body = json.loads(request.content)
        tx_id = f"SV3DS-{self._seq}"
        tx = {
            "idTransaccion": tx_id,
            "esReal": True,
            "urlCompletarPago3Ds": f"https://wompi.sv/3ds/{tx_id}",
            "monto": body.get("monto"),
            "idExterno": body.get("idExterno"),
        }
        self.transactions[tx_id] = tx
        return httpx.Response(200, json=tx)


def _wompi_provider(fake_api: FakeWompiAPI, **overrides) -> WompiPaymentProvider:
    kwargs = {
        "client_id": WOMPI_CLIENT_ID,
        "client_secret": WOMPI_CLIENT_SECRET,
        "environment": "sandbox",
        "tier_price_cents": 500,
        "http_client": httpx.Client(transport=httpx.MockTransport(fake_api.handle)),
    }
    kwargs.update(overrides)
    return WompiPaymentProvider(**kwargs)


def _wompi_transaction_event(
    *,
    estado: str,
    email: str,
    id_transaccion: str,
    id_suscripcion: str | None = None,
    id_externo: str | None = None,
) -> bytes:
    tx: dict = {
        "estado": estado,
        "idTransaccion": id_transaccion,
        "emailCliente": email,
        "monto": 5.0,
    }
    if id_suscripcion:
        tx["idSuscripcion"] = id_suscripcion
    if id_externo:
        tx["idExterno"] = id_externo
    return json.dumps({"data": {"transaccion": tx}}).encode()


def _signed_headers(body: bytes) -> dict[str, str]:
    return {"wompi_hash": compute_signature(body, WOMPI_CLIENT_SECRET)}


def _create_users(db):
    subscriber = User(
        email="wompi-sub@example.com",
        username="wompi-sub",
        hashed_password="not-used-in-tests",
        role=UserRole.registered,
        is_active=True,
    )
    creator = User(
        email="wompi-creator@example.com",
        username="wompi-creator",
        hashed_password="not-used-in-tests",
        role=UserRole.creator,
        is_active=True,
    )
    db.add_all([subscriber, creator])
    db.commit()
    db.refresh(subscriber)
    db.refresh(creator)
    return subscriber, creator


def _subscribe(db, provider):
    """Subscribe the fixture users; returns (row, subscriber email, link id)."""
    subscriber, creator = _create_users(db)
    service = SubscriptionService(db, provider=provider)
    subscription = service.create_subscription(
        subscriber.id,
        creator.id,
        plan_id="unused-for-wompi",
        success_url="https://example.com/success",
        cancel_url="https://example.com/cancel",
    )
    db.refresh(subscription)
    assert subscription.status == SubscriptionStatus.incomplete
    return subscription, subscriber.email, subscription.external_ref


# --------------------------------------------------------------------------- #
# Subscribe flow: per-subscription recurring link
# --------------------------------------------------------------------------- #

def test_wompi_subscribe_creates_pending_subscription_with_link(db_session):
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)

    with db_session as db:
        subscription, email, link_id = _subscribe(db, provider)

        assert subscription.payment_provider == "wompi"
        assert subscription.status == SubscriptionStatus.incomplete
        assert link_id.startswith("ENLACE-")
        assert subscription.checkout_url.startswith("https://wompi.sv/pagar/")

        # The gateway link carries the monthly price + billing day + a
        # subscriber-tagged name (support traceability).
        link = fake_api.links[link_id]
        assert link["monto"] == 5.0
        assert link["diaDePago"] == 1
        assert link["nombre"] == f"CCE-{subscription.subscriber_id}"


def test_wompi_subscribe_uses_configured_dia_de_pago(db_session):
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api, dia_de_pago=15, tier_price_cents=900)
    with db_session as db:
        subscription, _, link_id = _subscribe(db, provider)
        link = fake_api.links[link_id]
        assert link["diaDePago"] == 15
        assert link["monto"] == 9.0


# --------------------------------------------------------------------------- #
# Webhook signature validation (the acceptance's Wompi-signature check)
# --------------------------------------------------------------------------- #

def test_wompi_webhook_valid_signature_normalizes_event():
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)
    body = _wompi_transaction_event(
        estado="APROBADA",
        email="fan@example.com",
        id_transaccion="SV-1",
        id_suscripcion="SUS-1",
    )
    event = provider.verify_webhook(body, _signed_headers(body))
    assert event.event_type.value == "payment.succeeded"
    assert event.external_ref == "SUS-1"
    assert event.customer_email == "fan@example.com"
    assert event.subscription_status == "active"


def test_wompi_webhook_forged_signature_rejected():
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)
    body = _wompi_transaction_event(
        estado="APROBADA", email="fan@example.com", id_transaccion="SV-1"
    )
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook(body, {"wompi_hash": "0" * 64})


def test_wompi_webhook_unknown_estado_rejected():
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)
    body = _wompi_transaction_event(
        estado="PENDIENTE", email="fan@example.com", id_transaccion="SV-1"
    )
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook(body, _signed_headers(body))


def test_wompi_webhook_rejected_by_router(client, db_session, monkeypatch):
    """An invalid signature is a 400 at the endpoint, never reconciled."""
    monkeypatch.setattr(settings, "WOMPI_CLIENT_ID", WOMPI_CLIENT_ID)
    monkeypatch.setattr(settings, "WOMPI_CLIENT_SECRET", WOMPI_CLIENT_SECRET)
    body = _wompi_transaction_event(
        estado="APROBADA", email="fan@example.com", id_transaccion="SV-1"
    )
    headers = {"wompi_hash": "forged", "Content-Type": "application/json"}
    resp = client.post("/webhooks/wompi", data=body, headers=headers)
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Acceptance: a completed sandbox transaction activates the Subscription
# --------------------------------------------------------------------------- #

def test_wompi_webhook_activates_subscription_by_email(db_session):
    """APROBADA webhook -> the pending subscription becomes active.

    The recurring-charge event carries the suscripcion ref + payer email, not
    the link id we stored: the service matches by email. ``external_ref`` stays
    the link id — that is the resource cancellation disables.
    """
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)

    with db_session as db:
        subscription, email, link_id = _subscribe(db, provider)
        service = SubscriptionService(db, provider=provider)

        body = _wompi_transaction_event(
            estado="APROBADA",
            email=email,
            id_transaccion="SV-1",
            id_suscripcion="SUS-1",
        )
        event = service.handle_webhook(body, _signed_headers(body))
        assert event.duplicate is False
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        assert subscription.external_ref == link_id  # link id kept for cancel
        assert subscription.checkout_url is None

        # Redelivery of the same transaction is acked as duplicate.
        again = service.handle_webhook(body, _signed_headers(body))
        assert again.duplicate is True
        markers = db.scalars(
            select(ProcessedWebhookEvent).where(
                ProcessedWebhookEvent.provider == "wompi",
                ProcessedWebhookEvent.event_id == "SV-1",
            )
        ).all()
        assert len(markers) == 1


def test_wompi_renewal_reconciles_by_email_fallback(db_session):
    """A later renewal (new transaction, same subscriber) stays active.

    The active row is matched by the payer email (non-terminal statuses); the
    external_ref remains the link id throughout.
    """
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)

    with db_session as db:
        subscription, email, link_id = _subscribe(db, provider)
        service = SubscriptionService(db, provider=provider)
        first = _wompi_transaction_event(
            estado="APROBADA", email=email, id_transaccion="SV-1", id_suscripcion="SUS-1"
        )
        service.handle_webhook(first, _signed_headers(first))
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active

        renewal = _wompi_transaction_event(
            estado="APROBADA", email=email, id_transaccion="SV-2", id_suscripcion="SUS-1"
        )
        event = service.handle_webhook(renewal, _signed_headers(renewal))
        assert event.duplicate is False
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        assert subscription.external_ref == link_id  # untouched by renewals


def test_wompi_rejected_webhook_moves_active_to_past_due(db_session, monkeypatch):
    notifications: list = []
    monkeypatch.setattr(
        "app.services.subscriptions.enqueue_payment_failed_notification",
        lambda sub_id: notifications.append(sub_id),
    )
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)

    with db_session as db:
        subscription, email, _ = _subscribe(db, provider)
        service = SubscriptionService(db, provider=provider)
        approved = _wompi_transaction_event(
            estado="APROBADA", email=email, id_transaccion="SV-1", id_suscripcion="SUS-1"
        )
        service.handle_webhook(approved, _signed_headers(approved))
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active

        rejected = _wompi_transaction_event(
            estado="RECHAZADA", email=email, id_transaccion="SV-2", id_suscripcion="SUS-1"
        )
        service.handle_webhook(rejected, _signed_headers(rejected))
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.past_due
        assert notifications == [subscription.id]


def test_wompi_cancel_disables_link_and_marks_canceled(db_session):
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)

    with db_session as db:
        subscription, _, link_id = _subscribe(db, provider)
        service = SubscriptionService(db, provider=provider)

        service.cancel_subscription(subscription)
        assert fake_api.links[link_id]["estaProductivo"] is False
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.canceled


def test_wompi_cancel_after_activation_still_disables_the_link(db_session):
    """Regression: activating via webhook must not break later cancellation.

    The email-fallback match must NOT adopt the suscripcion ref over the link
    id — cancellation disables ``/EnlacePagoRecurrente/{link_id}`` and would
    404 if the ref were the suscripcion id.
    """
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)

    with db_session as db:
        subscription, email, link_id = _subscribe(db, provider)
        service = SubscriptionService(db, provider=provider)

        approved = _wompi_transaction_event(
            estado="APROBADA", email=email, id_transaccion="SV-1", id_suscripcion="SUS-1"
        )
        service.handle_webhook(approved, _signed_headers(approved))
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active

        service.cancel_subscription(subscription)
        assert fake_api.links[link_id]["estaProductivo"] is False
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.canceled


# --------------------------------------------------------------------------- #
# One-time charges (tokenized + 3DS)
# --------------------------------------------------------------------------- #

def test_wompi_charge_one_time_tokenized_succeeds():
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)
    result = provider.charge_one_time(
        ChargeRequest(
            amount_cents=500,
            currency="usd",
            payment_method_token="tok_test_1",
            metadata={"email": "fan@example.com", "customer_name": "Fan"},
        )
    )
    assert result.status == "succeeded"
    assert result.external_ref.startswith("SV-")
    assert result.raw["esAprobada"] is True


def test_wompi_charge_one_time_tokenized_failure():
    fake_api = FakeWompiAPI()
    fake_api.approve_next_charge = False
    provider = _wompi_provider(fake_api)
    result = provider.charge_one_time(
        ChargeRequest(amount_cents=500, payment_method_token="tok_test_1")
    )
    assert result.status == "failed"


def test_wompi_charge_requires_token():
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)
    with pytest.raises(PaymentProviderError):
        provider.charge_one_time(ChargeRequest(amount_cents=500))


def test_wompi_charge_3ds_returns_redirect_url():
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api, redirect_url="https://example.com/return")
    result = provider.charge_one_time_3ds(
        ChargeRequest(amount_cents=700, payment_method_token="tok_3ds_1"),
        cvv="123",
        billing={
            "nombre": "Fan",
            "apellido": "Uno",
            "email": "fan@example.com",
            "ciudad": "San Salvador",
            "direccion": "Av. 1",
            "idPais": "SV",
            "idRegion": "SS",
            "codigoPostal": "1101",
            "telefono": "+50370000000",
        },
    )
    assert result.status == "pending"  # completes via webhook after 3DS
    assert result.raw["urlCompletarPago3Ds"].startswith("https://wompi.sv/3ds/")


def test_wompi_charge_3ds_requires_redirect_url():
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api, redirect_url="")
    with pytest.raises(PaymentProviderError):
        provider.charge_one_time_3ds(
            ChargeRequest(amount_cents=700, payment_method_token="tok_3ds_1"),
            cvv="123",
            billing={"nombre": "Fan"},
        )


# --------------------------------------------------------------------------- #
# Configuration guards
# --------------------------------------------------------------------------- #

def test_wompi_requires_credentials():
    from app.payments import ProviderConfigurationError

    with pytest.raises(ProviderConfigurationError):
        WompiPaymentProvider(client_id="", client_secret="")


def test_wompi_environment_must_be_sandbox_or_production():
    from app.payments import ProviderConfigurationError

    with pytest.raises(ProviderConfigurationError):
        WompiPaymentProvider(
            client_id="a", client_secret="b", environment="mars"
        )


# --------------------------------------------------------------------------- #
# Real sandbox (opt-in — requires a Wompi SV test applicativo)
# --------------------------------------------------------------------------- #

_HAS_WOMPI_CREDS = all(
    os.environ.get(var) for var in ("WOMPI_CLIENT_ID", "WOMPI_CLIENT_SECRET")
)
_REQUIRE_SANDBOX = os.environ.get("RUN_WOMPI_SANDBOX") == "1"


@pytest.mark.skipif(
    not (_HAS_WOMPI_CREDS and _REQUIRE_SANDBOX),
    reason=(
        "Real Wompi sandbox test: set WOMPI_CLIENT_ID / WOMPI_CLIENT_SECRET "
        "(a sandbox applicativo from the Wompi dashboard) and RUN_WOMPI_SANDBOX=1. "
        "The customer must complete the hosted payment; webhook activation is "
        "exercised by the simulated tests above."
    ),
)
def test_wompi_sandbox_subscribe_flow(db_session):
    """Real sandbox: create a recurring link + subscription against api.wompi.sv.

    Assertions stop at the pending row + hosted link — the payment is a
    human/browser step on Wompi's page; once paid, the webhook pipeline tested
    above activates the subscription.
    """
    provider = WompiPaymentProvider(
        client_id=os.environ["WOMPI_CLIENT_ID"],
        client_secret=os.environ["WOMPI_CLIENT_SECRET"],
        environment="sandbox",
        tier_price_cents=settings.SUBSCRIPTION_TIER_PRICE_CENTS,
        dia_de_pago=settings.WOMPI_DIA_DE_PAGO,
    )
    try:
        with db_session as db:
            subscription, email, link_id = _subscribe(db, provider)
            assert subscription.payment_provider == "wompi"
            assert link_id.startswith("ENLACE-")
            assert "wompi.sv" in subscription.checkout_url
            print(
                f"\n[wompi sandbox] customer subscribes at: {subscription.checkout_url}\n"
                f"[wompi sandbox] configure the webhook POST /api/webhooks/wompi in the\n"
                f"[wompi sandbox] dashboard; Wompi signs events with the wompi_hash header."
            )
    finally:
        provider._client.close()
