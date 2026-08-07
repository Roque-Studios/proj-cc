"""Wompi integration tests.

The real Wompi API is simulated with ``httpx.MockTransport`` injected into
``pywompi.WompiClient(http_client=...)``, so the provider's real request
building, OAuth2 client-credentials auth, and webhook-signature validation
(``wompi_hash`` = HMAC-SHA256 of the raw body with the API secret) run against
a faithful fake of the Swagger (payment links, tokenized transactions, 3DS).
Covers the acceptance criteria:

- subscribing creates an ``incomplete`` local row with the hosted **payment
  link** as checkout url (``external_ref`` = link id; the link carries our
  creator id as ``identificadorEnlaceComercio``, ``nombreProducto`` =
  "subscription to <creator tag>", and ``configuracion.urlWebhook`` /
  ``urlRedirect`` so Wompi notifies us and returns the customer to checkout);
- a signature-valid webhook activates the subscription — the flat Wompi SV
  payment-link payload (``ResultadoTransaccion`` / ``EnlacePago`` /
  ``cliente.Email``) matched directly by the echoed link id, otherwise by
  (creator, payer email) via the merchant reference — the *sandbox transaction
  completes and activates the Subscription* acceptance;
- forged signatures are rejected before anything is processed;
- the legacy nested ``data.transaccion.estado`` webhook shape still parses;
- renewals reconcile for the same creator; failures move to ``past_due``;
- one-time (tokenized, no 3DS) and 3DS redirect charges work, and a one-time
  transaction webhook never reconciles as a monthly payment.

A real-sandbox test (``test_wompi_sandbox_subscribe_flow``) runs only when
``WOMPI_CLIENT_ID`` / ``WOMPI_CLIENT_SECRET`` are provided **and**
``RUN_WOMPI_SANDBOX=1``; it creates a real payment link and asserts the
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
    ProviderConfigurationError,
    WebhookVerificationError,
)
from app.payments.wompi import WompiPaymentProvider
from app.services.subscriptions import SubscriptionService

WOMPI_CLIENT_ID = "app_wompi_test"
WOMPI_CLIENT_SECRET = "wompi_api_secret_test"
WOMPI_WEBHOOK_URL = "https://example.com/api/webhooks/wompi"
WOMPI_REDIRECT_URL = "https://example.com/checkout"


# --------------------------------------------------------------------------- #
# A fake Wompi sandbox (httpx.MockTransport handler)
# --------------------------------------------------------------------------- #

class FakeWompiAPI:
    """In-memory Wompi: OAuth, payment links, tokenized/3DS transactions."""

    def __init__(self) -> None:
        self.links: dict[int, dict] = {}
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
        if request.method == "POST" and path == "/EnlacePago":
            return self._create_payment_link(request)
        if request.method == "POST" and path == "/TransaccionCompra/TokenizadaSin3Ds":
            return self._tokenized_charge(request)
        if request.method == "POST" and path == "/TransaccionCompra/3Ds":
            return self._charge_3ds(request)
        return httpx.Response(404, text='{"mensaje": "not found"}')

    def _create_payment_link(self, request: httpx.Request) -> httpx.Response:
        self._seq += 1
        body = json.loads(request.content)
        link_id = self._seq  # real Wompi link ids are integers
        link = {
            "idEnlace": link_id,
            "identificadorEnlaceComercio": body.get("identificadorEnlaceComercio"),
            "nombreProducto": body.get("nombreProducto"),
            "monto": body.get("monto"),
            "configuracion": body.get("configuracion"),
            "urlEnlace": f"https://wompi.sv/pagar/{link_id}",
            "urlEnlaceLargo": f"https://wompi.sv/pagar/largo/{link_id}",
            "urlQrCodeEnlace": f"https://wompi.sv/qr/{link_id}",
        }
        self.links[link_id] = link
        return httpx.Response(200, json=link)

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
        "webhook_url": WOMPI_WEBHOOK_URL,
        "redirect_url": WOMPI_REDIRECT_URL,
        "http_client": httpx.Client(transport=httpx.MockTransport(fake_api.handle)),
    }
    kwargs.update(overrides)
    return WompiPaymentProvider(**kwargs)


def _wompi_payment_link_event(
    *,
    resultado: str = "ExitosaAprobada",
    email: str,
    id_transaccion: str,
    id_enlace: int | None = None,
    identificador_enlace: str | None = None,
) -> bytes:
    """The flat Wompi SV payment-link webhook body (official docs shape)."""
    payload: dict = {
        "IdCuenta": "acct_1",
        "FechaTransaccion": "2026-08-07T12:00:00-06:00",
        "Monto": 5.0,
        "ModuloUtilizado": "BotonPago",
        "IdTransaccion": id_transaccion,
        "ResultadoTransaccion": resultado,
        "Cantidad": 1,
        "EsProductiva": False,
        "cliente": {"Nombre": "Fan", "Email": email},
    }
    if id_enlace is not None or identificador_enlace is not None:
        payload["EnlacePago"] = {
            "Id": id_enlace,
            "IdentificadorEnlaceComercio": identificador_enlace,
            "NombreProducto": "subscription to creator",
        }
    return json.dumps(payload).encode()


def _wompi_legacy_event(
    *,
    estado: str,
    email: str,
    id_transaccion: str,
    id_suscripcion: str | None = None,
) -> bytes:
    """The legacy nested ``data.transaccion.estado`` webhook shape."""
    tx: dict = {
        "estado": estado,
        "idTransaccion": id_transaccion,
        "emailCliente": email,
        "monto": 5.0,
    }
    if id_suscripcion:
        tx["idSuscripcion"] = id_suscripcion
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
        success_url=WOMPI_REDIRECT_URL,
        cancel_url=WOMPI_REDIRECT_URL,
    )
    db.refresh(subscription)
    assert subscription.status == SubscriptionStatus.incomplete
    return subscription, subscriber.email, subscription.external_ref


# --------------------------------------------------------------------------- #
# Subscribe flow: per-subscription hosted payment link
# --------------------------------------------------------------------------- #

def test_wompi_subscribe_creates_pending_subscription_with_link(db_session):
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)

    with db_session as db:
        subscription, email, link_id = _subscribe(db, provider)

        assert subscription.payment_provider == "wompi"
        assert subscription.status == SubscriptionStatus.incomplete
        assert link_id.isdigit()
        assert subscription.checkout_url.startswith("https://wompi.sv/pagar/")

        # The gateway payment link carries the merchant reference (our creator
        # id), the product name ("subscription to <creator tag>"), the price —
        # and the webhook + redirect so Wompi notifies the backend and returns
        # the customer to the checkout page after paying.
        creator = db.get(User, subscription.creator_id)
        link = fake_api.links[int(link_id)]
        assert link["identificadorEnlaceComercio"] == str(subscription.creator_id)
        assert link["nombreProducto"] == f"subscription to {creator.username}"
        assert link["monto"] == 5.0
        configuracion = link["configuracion"]
        assert configuracion["urlWebhook"] == WOMPI_WEBHOOK_URL
        assert configuracion["urlRedirect"] == WOMPI_REDIRECT_URL
        assert configuracion["notificarTransaccionCliente"] is True


def test_wompi_subscribe_uses_configured_price(db_session):
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api, tier_price_cents=900)
    with db_session as db:
        subscription, _, link_id = _subscribe(db, provider)
        assert fake_api.links[int(link_id)]["monto"] == 9.0


def test_wompi_subscribe_uses_configured_redirect_fallback(db_session):
    """Without a per-checkout success url the configured redirect is used."""
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api, redirect_url="https://example.com/return")
    with db_session as db:
        subscriber, creator = _create_users(db)
        service = SubscriptionService(db, provider=provider)
        subscription = service.create_subscription(
            subscriber.id, creator.id, plan_id="unused-for-wompi"
        )
        link = fake_api.links[int(subscription.external_ref)]
        assert link["configuracion"]["urlRedirect"] == "https://example.com/return"


def test_wompi_subscribe_requires_webhook_url(db_session):
    """A payment link without a webhook url can never be reconciled — fail fast."""
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api, webhook_url="")
    with db_session as db:
        subscriber, creator = _create_users(db)
        service = SubscriptionService(db, provider=provider)
        with pytest.raises(ProviderConfigurationError):
            service.create_subscription(
                subscriber.id, creator.id, plan_id="unused-for-wompi"
            )


# --------------------------------------------------------------------------- #
# Webhook signature validation (the acceptance's Wompi-signature check)
# --------------------------------------------------------------------------- #

def test_wompi_webhook_valid_signature_normalizes_event():
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)
    body = _wompi_payment_link_event(
        email="fan@example.com",
        id_transaccion="SV-1",
        id_enlace=15,
        identificador_enlace="42",
    )
    event = provider.verify_webhook(body, _signed_headers(body))
    assert event.event_type.value == "payment.succeeded"
    assert event.external_ref == "15"  # the echoed link id
    assert event.customer_email == "fan@example.com"
    assert event.subscription_status == "active"
    assert event.recurring is True
    assert event.metadata["creator_id"] == "42"


def test_wompi_webhook_legacy_shape_still_supported():
    """The old ``data.transaccion.estado`` shape keeps parsing."""
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)
    body = _wompi_legacy_event(
        estado="APROBADA", email="fan@example.com", id_transaccion="SV-1",
        id_suscripcion="SUS-1",
    )
    event = provider.verify_webhook(body, _signed_headers(body))
    assert event.event_type.value == "payment.succeeded"
    assert event.external_ref == "SUS-1"
    assert event.customer_email == "fan@example.com"


def test_wompi_webhook_forged_signature_rejected():
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)
    body = _wompi_payment_link_event(
        email="fan@example.com", id_transaccion="SV-1", id_enlace=15
    )
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook(body, {"wompi_hash": "0" * 64})


def test_wompi_webhook_without_result_rejected():
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)
    body = json.dumps(
        {"IdTransaccion": "SV-1", "cliente": {"Email": "fan@example.com"}}
    ).encode()
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook(body, _signed_headers(body))


def test_wompi_webhook_unknown_legacy_estado_rejected():
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)
    body = _wompi_legacy_event(
        estado="PENDIENTE", email="fan@example.com", id_transaccion="SV-1"
    )
    with pytest.raises(WebhookVerificationError):
        provider.verify_webhook(body, _signed_headers(body))


def test_wompi_webhook_rejected_by_router(client, db_session, monkeypatch):
    """An invalid signature is a 400 at the endpoint, never reconciled."""
    monkeypatch.setattr(settings, "WOMPI_CLIENT_ID", WOMPI_CLIENT_ID)
    monkeypatch.setattr(settings, "WOMPI_CLIENT_SECRET", WOMPI_CLIENT_SECRET)
    body = _wompi_payment_link_event(
        email="fan@example.com", id_transaccion="SV-1", id_enlace=15
    )
    headers = {"wompi_hash": "forged", "Content-Type": "application/json"}
    resp = client.post("/webhooks/wompi", data=body, headers=headers)
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Acceptance: a completed sandbox transaction activates the Subscription
# --------------------------------------------------------------------------- #

def test_wompi_webhook_activates_subscription_by_link_ref(db_session):
    """The flat webhook echoes the link id -> the stored ref matches directly."""
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)

    with db_session as db:
        subscription, email, link_id = _subscribe(db, provider)
        service = SubscriptionService(db, provider=provider)

        body = _wompi_payment_link_event(
            email=email,
            id_transaccion="SV-1",
            id_enlace=int(link_id),
            identificador_enlace=str(subscription.creator_id),
        )
        event = service.handle_webhook(body, _signed_headers(body))
        assert event.duplicate is False
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        assert subscription.external_ref == link_id  # link id kept
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


def test_wompi_webhook_activates_subscription_by_creator_and_email(db_session):
    """APROBADA webhook -> the pending subscription becomes active.

    When Wompi omits the link id from the event, the merchant reference (our
    creator id) + payer email match the right row. ``external_ref`` stays the
    link id.
    """
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)

    with db_session as db:
        subscription, email, link_id = _subscribe(db, provider)
        service = SubscriptionService(db, provider=provider)

        body = _wompi_payment_link_event(
            email=email,
            id_transaccion="SV-1",
            identificador_enlace=str(subscription.creator_id),
        )
        event = service.handle_webhook(body, _signed_headers(body))
        assert event.duplicate is False
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        assert subscription.external_ref == link_id


def test_wompi_payment_link_event_activates_the_right_creator(db_session):
    """The merchant ref pins reconciliation to the charged creator.

    A subscriber with pending rows for two creators gets only the charged
    creator's row activated — without the creator pin the email fallback
    would pick the latest non-terminal row (the wrong creator).
    """
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)

    with db_session as db:
        subscriber = User(
            email="wompi-sub@example.com",
            username="wompi-sub",
            hashed_password="not-used-in-tests",
            role=UserRole.registered,
            is_active=True,
        )
        creator_a = User(
            email="wompi-a@example.com",
            username="wompi-a",
            hashed_password="not-used-in-tests",
            role=UserRole.creator,
            is_active=True,
        )
        creator_b = User(
            email="wompi-b@example.com",
            username="wompi-b",
            hashed_password="not-used-in-tests",
            role=UserRole.creator,
            is_active=True,
        )
        db.add_all([subscriber, creator_a, creator_b])
        db.commit()
        db.refresh(subscriber)
        db.refresh(creator_a)
        db.refresh(creator_b)

        service = SubscriptionService(db, provider=provider)
        sub_a = service.create_subscription(
            subscriber.id, creator_a.id, plan_id="unused-for-wompi"
        )
        sub_b = service.create_subscription(
            subscriber.id, creator_b.id, plan_id="unused-for-wompi"
        )
        assert sub_a.id < sub_b.id  # creator_a is the older row

        body = _wompi_payment_link_event(
            email=subscriber.email,
            id_transaccion="SV-1",
            identificador_enlace=str(creator_a.id),
        )
        service.handle_webhook(body, _signed_headers(body))
        db.refresh(sub_a)
        db.refresh(sub_b)
        assert sub_a.status == SubscriptionStatus.active
        assert sub_b.status == SubscriptionStatus.incomplete


def test_wompi_one_time_charge_event_never_reconciles_as_monthly(db_session):
    """A one-time API-transaction webhook (no EnlacePago block) is ignored.

    ``TransaccionCompra`` webhooks carry the payer email but no ``EnlacePago``
    block: without the ``recurring`` gate the email fallback would reconcile
    the one-time unlock against the subscriber's active subscription and
    record a spurious *monthly* payment in the revenue ledger. The event must
    be a no-op instead — the subscription stays untouched and no ``Payment``
    row is written.
    """
    from app.models import Payment

    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)

    with db_session as db:
        subscription, email, link_id = _subscribe(db, provider)
        service = SubscriptionService(db, provider=provider)
        first = _wompi_payment_link_event(
            email=email,
            id_transaccion="SV-1",
            identificador_enlace=str(subscription.creator_id),
        )
        service.handle_webhook(first, _signed_headers(first))
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        # The activation legitimately records the first month.
        assert len(db.scalars(select(Payment)).all()) == 1

        # One-time unlock charge webhook: same payer email, no EnlacePago block.
        one_time = _wompi_payment_link_event(
            email=email, id_transaccion="SV-100"
        )
        event = service.handle_webhook(one_time, _signed_headers(one_time))
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        assert subscription.external_ref == link_id

        # Still exactly one payment: the one-time event added nothing.
        assert len(db.scalars(select(Payment)).all()) == 1


def test_wompi_renewal_reconciles_by_creator_and_email(db_session):
    """A later payment (new transaction, same subscriber+creator) stays active.

    The active row is matched by (merchant ref, payer email); the
    external_ref remains the link id throughout.
    """
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)

    with db_session as db:
        subscription, email, link_id = _subscribe(db, provider)
        service = SubscriptionService(db, provider=provider)
        first = _wompi_payment_link_event(
            email=email,
            id_transaccion="SV-1",
            identificador_enlace=str(subscription.creator_id),
        )
        service.handle_webhook(first, _signed_headers(first))
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active

        renewal = _wompi_payment_link_event(
            email=email,
            id_transaccion="SV-2",
            identificador_enlace=str(subscription.creator_id),
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
        approved = _wompi_payment_link_event(
            email=email,
            id_transaccion="SV-1",
            identificador_enlace=str(subscription.creator_id),
        )
        service.handle_webhook(approved, _signed_headers(approved))
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active

        rejected = _wompi_payment_link_event(
            resultado="Rechazada",
            email=email,
            id_transaccion="SV-2",
            identificador_enlace=str(subscription.creator_id),
        )
        service.handle_webhook(rejected, _signed_headers(rejected))
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.past_due
        assert notifications == [subscription.id]


def test_wompi_cancel_marks_subscription_canceled_locally(db_session):
    """One-time links can't re-charge — cancel is local-only.

    There is no recurring charge at the provider to disable, so cancellation
    only marks the local row canceled (no provider call happens).
    """
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)

    with db_session as db:
        subscription, _, _ = _subscribe(db, provider)
        service = SubscriptionService(db, provider=provider)

        service.cancel_subscription(subscription)
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.canceled


def test_wompi_cancel_after_activation_marks_subscription_canceled(db_session):
    """Activating via webhook must not break later cancellation."""
    fake_api = FakeWompiAPI()
    provider = _wompi_provider(fake_api)

    with db_session as db:
        subscription, email, _ = _subscribe(db, provider)
        service = SubscriptionService(db, provider=provider)

        approved = _wompi_payment_link_event(
            email=email,
            id_transaccion="SV-1",
            identificador_enlace=str(subscription.creator_id),
        )
        service.handle_webhook(approved, _signed_headers(approved))
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active

        service.cancel_subscription(subscription)
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
            "ciudad": "Ciudad de México",
            "direccion": "Av. 1",
            "idPais": "MX",
            "idRegion": "DF",
            "codigoPostal": "06600",
            "telefono": "+525512345678",
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
# Real sandbox (opt-in — requires a Wompi test applicativo)
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
    """Real sandbox: create a payment link + subscription against api.wompi.sv.

    Assertions stop at the pending row + hosted link — the payment is a
    human/browser step on Wompi's page; once paid, the webhook pipeline tested
    above activates the subscription.
    """
    provider = WompiPaymentProvider(
        client_id=os.environ["WOMPI_CLIENT_ID"],
        client_secret=os.environ["WOMPI_CLIENT_SECRET"],
        environment="sandbox",
        tier_price_cents=settings.SUBSCRIPTION_TIER_PRICE_CENTS,
        webhook_url=settings.WOMPI_WEBHOOK_URL,
        redirect_url=settings.WOMPI_REDIRECT_URL or settings.WOMPI_3DS_REDIRECT_URL,
    )
    try:
        with db_session as db:
            subscription, email, link_id = _subscribe(db, provider)
            assert subscription.payment_provider == "wompi"
            assert str(link_id).isdigit()
            assert "wompi.sv" in subscription.checkout_url
            print(
                f"\n[wompi sandbox] customer pays at: {subscription.checkout_url}\n"
                f"[wompi sandbox] register the webhook POST /api/webhooks/wompi in the\n"
                f"[wompi sandbox] dashboard (or set WOMPI_WEBHOOK_URL so payment links\n"
                f"[wompi sandbox] carry it in configuracion.urlWebhook); Wompi signs\n"
                f"[wompi sandbox] events with the wompi_hash header."
            )
    finally:
        provider._client.close()
