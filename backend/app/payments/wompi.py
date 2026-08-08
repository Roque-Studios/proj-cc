"""Wompi payment gateway implementation.

Wompi authenticates with **OAuth2 client credentials** (App ID / API Secret)
instead of public/private keys, and signs webhooks with the ``wompi_hash``
header — the HMAC-SHA256 of the **raw** body keyed with the API Secret. The
`pywompi` package handles the OAuth token caching, the generic authenticated
``request(method, path, json=)`` (used for every endpoint below), and webhook
validation via ``parse_event(raw_body, received_hash, api_secret)``.

Payment model (from the Wompi Swagger):

- **Subscription** — ``POST /EnlacePago`` (via ``pywompi``'s
  ``WompiClient.create_payment_link``) creates a hosted **one-time payment
  link** (``identificadorEnlaceComercio`` = our creator id, ``nombreProducto``
  = "subscription to <creator tag>", ``monto``) whose ``configuracion``
  carries ``urlWebhook`` (our ``/api/webhooks/wompi`` endpoint — required,
  Wompi only notifies payment links through it) and ``urlRedirect`` (where
  the customer returns after paying). The customer pays on Wompi's page (3DS
  is handled there) and the payment arrives as a **flat transaction
  webhook** (``ResultadoTransaccion`` = "ExitosaAprobada",
  ``EnlacePago.{Id, IdentificadorEnlaceComercio}``, ``cliente.Email``). The
  link id doubles as our ``external_ref``; because a one-time link never
  auto-charges, cancellation is local-only. (Wompi's recurring-link endpoint
  ``EnlacePagoRecurrente`` was creating bad states with no fix ETA, so
  subscriptions use payment links instead.)
- **One-time** — ``POST /TransaccionCompra/TokenizadaSin3Ds`` charges a
  previously tokenized card (no 3DS); ``POST /TransaccionCompra/3Ds`` starts
  a 3DS flow returning ``urlCompletarPago3Ds`` for the customer to
  authenticate.
- **Webhooks** — Wompi SV signs every webhook with the ``wompi_hash`` header
  (HMAC-SHA256 of the raw body) and notifies **payment-link** transactions
  with a flat payload: ``ResultadoTransaccion`` = "ExitosaAprobada" on
  success, ``EnlacePago.Id`` = the link id we stored, ``EnlacePago.
  IdentificadorEnlaceComercio`` = our creator id, ``cliente.Email`` = the
  payer. The provider surfaces those as ``external_ref`` / ``creator_id`` /
  ``customer_email`` so the service reconciles by link id directly, or by
  (creator, email) as a fallback — see ``SubscriptionService``. The legacy
  nested ``data.transaccion.estado`` shape is still parsed for
  compatibility.

The environment (sandbox vs production) is a property of the Wompi applicativo
(the credentials you configure), not a URL switch; ``api_base_url`` /
``token_url`` are overridable for test accounts.
"""

from __future__ import annotations

from typing import Mapping

from pywompi import WompiClient, parse_event
from pywompi.exceptions import WompiAPIError, WompiAuthError, WompiConnectionError, WompiWebhookError

from .base import (
    ChargeRequest,
    ChargeResult,
    PaymentLinkResult,
    PaymentProvider,
    PaymentProviderError,
    ProviderConfigurationError,
    SubscriptionIntent,
    SubscriptionResult,
    WebhookEvent,
    WebhookEventType,
    WebhookVerificationError,
)

_DEFAULT_API_BASE_URL = "https://api.wompi.sv"
_DEFAULT_TOKEN_URL = "https://id.wompi.sv/connect/token"

# Wompi transaction estado -> our normalized event vocabulary.
_ESTADO_MAP = {
    "APROBADA": (WebhookEventType.payment_succeeded, "active"),
    "RECHAZADA": (WebhookEventType.payment_failed, None),
}


class WompiPaymentProvider(PaymentProvider):
    name = "wompi"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        environment: str = "sandbox",
        api_base_url: str = _DEFAULT_API_BASE_URL,
        token_url: str = _DEFAULT_TOKEN_URL,
        tier_price_cents: int = 500,
        # The absolute, publicly reachable URL of the backend's
        # ``/api/webhooks/wompi`` endpoint — sent as the payment link's
        # ``configuracion.urlWebhook`` (required at subscription time).
        webhook_url: str = "",
        redirect_url: str = "",
        timeout: float = 10.0,
        http_client=None,
    ) -> None:
        if not client_id or not client_secret:
            raise ProviderConfigurationError(
                "Wompi client id and secret are required (WOMPI_CLIENT_ID / WOMPI_CLIENT_SECRET)"
            )
        if environment not in ("sandbox", "production"):
            raise ProviderConfigurationError(
                f"WOMPI_ENVIRONMENT must be 'sandbox' or 'production', got '{environment}'"
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.environment = environment
        self.tier_price_cents = tier_price_cents
        self.webhook_url = webhook_url
        self.redirect_url = redirect_url
        # ``http_client`` is injected in tests (httpx.MockTransport); pywompi
        # caches the OAuth2 token on the instance, so reuse it per process.
        self._client = WompiClient(
            client_id=client_id,
            client_secret=client_secret,
            token_url=token_url,
            api_base_url=api_base_url,
            timeout=timeout,
            http_client=http_client,
        )

    # ------------------------------------------------------------------ #
    # Interface
    # ------------------------------------------------------------------ #

    def create_customer(
        self, email: str, name: str | None = None, metadata: dict | None = None
    ) -> str:
        """Wompi has no customer object; the email is the stable identity."""
        return f"wompi_customer_{email}"

    def create_subscription(self, intent: SubscriptionIntent) -> SubscriptionResult:
        """Create a hosted one-time payment link for the subscription.

        Wompi's recurring-link endpoint (``EnlacePagoRecurrente``) was creating
        bad states with no fix ETA, so subscriptions use a **payment link**
        instead (``pywompi.WompiClient.create_payment_link`` → ``POST
        /EnlacePago``): the customer pays ``tier_price_cents`` on Wompi's
        hosted page (3DS handled there) and the payment arrives by webhook.
        ``identificadorEnlaceComercio`` is our creator id — the merchant
        reference the webhook echoes back — and ``nombreProducto`` names the
        product for the customer's statement / Wompi dashboard. The link's
        ``configuracion`` carries ``urlWebhook`` (the backend's
        ``/api/webhooks/wompi`` — without it Wompi never notifies us, so a
        paid subscription would never activate) and ``urlRedirect`` (the
        subscriber's return URL: the checkout success url, else the configured
        redirect). The link id doubles as our ``external_ref``; since a
        one-time link never auto-charges, cancellation is local-only (see
        :meth:`cancel_subscription`).
        """
        if not self.webhook_url:
            raise ProviderConfigurationError(
                "WOMPI_WEBHOOK_URL is required for Wompi payment links — Wompi "
                "notifies the backend through it (POST /api/webhooks/wompi). "
                "Enter it in the gateway settings before enabling the gateway."
            )
        creator_id = intent.metadata.get("creator_id", "?")
        creator_username = intent.metadata.get("creator_username") or creator_id
        configuracion: dict = {
            "urlWebhook": self.webhook_url,
            "notificarTransaccionCliente": True,
        }
        redirect = intent.success_url or self.redirect_url
        if redirect:
            configuracion["urlRedirect"] = redirect
        if intent.cancel_url:
            configuracion["urlRetorno"] = intent.cancel_url
        link = self._client.create_payment_link(
            {
                "identificadorEnlaceComercio": creator_id,
                "nombreProducto": f"subscription to {creator_username}",
                "monto": self.tier_price_cents / 100,
                "configuracion": configuracion,
            }
        )
        return SubscriptionResult(
            external_ref=link["idEnlace"],
            status="incomplete",
            checkout_url=link.get("urlEnlace"),
            raw=link,
        )

    def cancel_subscription(self, external_ref: str) -> None:
        """Cancel a subscription locally — a one-time link can't re-charge.

        A payment link is paid once and has no recurring charge at the provider
        to disable, so cancellation is local-only (``SubscriptionService``
        marks the row ``canceled``). No provider call is made; ``external_ref``
        is accepted for interface parity.
        """
        _ = external_ref  # intentionally unused — nothing to disable at Wompi
    def cancel_at_period_end(self, external_ref: str) -> None:
        """Wompi has no cancel-at-period-end; best-effort no-op.

        The local ``cancel_at_period_end`` flag drives access revocation via
        the scheduled expiry task.
        """

    def verify_webhook(
        self, body: bytes, headers: Mapping[str, str]
    ) -> WebhookEvent:
        """Validate the ``wompi_hash`` signature and normalize the event.

        The HMAC covers the raw body byte-for-byte (never re-serialize the
        parsed JSON), exactly as ``pywompi.parse_event`` enforces.

        Wompi SV notifies payment-link transactions with a **flat** payload
        (``ResultadoTransaccion`` / ``EnlacePago`` / ``cliente``); the legacy
        nested ``data.transaccion.estado`` shape is still parsed for
        compatibility.
        """
        try:
            event = parse_event(
                raw_body=body,
                received_hash=headers.get("wompi_hash", ""),
                api_secret=self.client_secret,
            )
        except WompiWebhookError as exc:
            raise WebhookVerificationError(f"Invalid Wompi webhook signature: {exc}") from exc

        data = event.get("data") if isinstance(event.get("data"), dict) else None
        if isinstance(data, dict) and "transaccion" in data:
            return self._normalize_transaccion_event(event, data["transaccion"])
        return self._normalize_payment_link_event(event)

    def _normalize_transaccion_event(self, event: dict, tx: dict) -> WebhookEvent:
        """Normalize a legacy ``data.transaccion`` event (recurring links)."""
        estado = tx.get("estado", "")
        mapped = _ESTADO_MAP.get(estado)
        if mapped is None:
            raise WebhookVerificationError(f"Unhandled Wompi transaction estado: {estado}")
        event_type, subscription_status = mapped

        # A subscription-relevant charge is identifiable by the subscription /
        # merchant ref (``idSuscripcion`` / ``identificadorEnlaceComercio``); a
        # plain one-time ``TransaccionCompra`` event carries only the
        # transaction id. The distinction gates the service's email fallback so
        # a one-time purchase event never reconciles as a monthly payment.
        recurring = bool(
            tx.get("idSuscripcion") or tx.get("identificadorEnlaceComercio")
        )

        return WebhookEvent(
            provider=self.name,
            event_type=event_type,
            external_ref=(
                tx.get("idSuscripcion")
                or tx.get("idEnlace")
                or tx.get("idExterno")
                or tx.get("idTransaccion")
            ),
            id=event.get("id") or tx.get("idTransaccion"),
            subscription_status=subscription_status,
            customer_email=tx.get("emailCliente"),
            recurring=recurring,
            metadata={
                "idTransaccion": tx.get("idTransaccion"),
                "estado": estado,
                "monto": tx.get("monto"),
                "creator_id": tx.get("identificadorEnlaceComercio"),
            },
            raw=event,
        )

    def _normalize_payment_link_event(self, event: dict) -> WebhookEvent:
        """Normalize a Wompi SV payment-link webhook (the flat payload).

        ``ResultadoTransaccion`` = "ExitosaAprobada" marks a successful
        payment; ``EnlacePago.Id`` is the link id we stored (direct ref
        match), ``EnlacePago.IdentificadorEnlaceComercio`` our creator id, and
        ``cliente.Email`` the payer. The ``EnlacePago`` block also marks the
        event as subscription-relevant — a one-time API transaction webhook
        (no block) is gated out of the email fallback.
        """
        resultado = str(event.get("ResultadoTransaccion") or "")
        enlace = event.get("EnlacePago") or {}
        if resultado.startswith("Exitosa"):
            event_type, subscription_status = (
                WebhookEventType.payment_succeeded,
                "active",
            )
        elif not resultado:
            raise WebhookVerificationError(
                "Wompi webhook payload has no ResultadoTransaccion"
            )
        else:
            event_type, subscription_status = WebhookEventType.payment_failed, None

        return WebhookEvent(
            provider=self.name,
            event_type=event_type,
            # The link id (``EnlacePago.Id``) matches our stored external_ref
            # directly; the merchant ref + payer email cover the (creator,
            # email) fallback when Wompi omits it.
            external_ref=str(
                enlace.get("Id") or event.get("IdTransaccion") or ""
            )
            or None,
            id=str(event.get("IdTransaccion") or event.get("IdIntentoPago") or "")
            or None,
            subscription_status=subscription_status,
            customer_email=(event.get("cliente") or {}).get("Email"),
            recurring=bool(enlace),
            metadata={
                "idTransaccion": event.get("IdTransaccion"),
                "resultado": resultado,
                "monto": event.get("Monto"),
                "idEnlace": enlace.get("Id"),
                "creator_id": enlace.get("IdentificadorEnlaceComercio"),
            },
            raw=event,
        )

    def create_one_time_link(self, request: ChargeRequest) -> PaymentLinkResult:
        """Create a hosted one-time payment link (redirect checkout).

        Wompi has no client-side card collection for unlocks on this platform,
        so one-time payments use the same hosted **payment link** mechanism as
        subscriptions: the customer pays on Wompi's page (3DS handled there)
        and the payment arrives as a flat transaction webhook — the link id
        doubles as the ``external_ref`` the local unlock row stores. The
        merchant reference encodes ``unlock_{post_id}`` and the product name
        describes the unlock so the Wompi dashboard stays readable.
        """
        if not self.webhook_url:
            raise ProviderConfigurationError(
                "WOMPI_WEBHOOK_URL is required for one-time payment links — Wompi "
                "notifies the backend through it (POST /api/webhooks/wompi). "
                "Enter it in the gateway settings before enabling the gateway."
            )
        post_id = request.metadata.get("post_id") or request.metadata.get("message_id") or ""
        configuracion: dict = {
            "urlWebhook": self.webhook_url,
            "notificarTransaccionCliente": True,
        }
        redirect = request.success_url or self.redirect_url
        if redirect:
            configuracion["urlRedirect"] = redirect
        if request.cancel_url:
            configuracion["urlRetorno"] = request.cancel_url
        link = self._client.create_payment_link(
            {
                "identificadorEnlaceComercio": f"unlock_{post_id}" if post_id else "unlock",
                "nombreProducto": request.description
                or f"Unlock content {post_id}".strip(),
                "monto": request.amount_cents / 100,
                "configuracion": configuracion,
            }
        )
        return PaymentLinkResult(
            external_ref=str(link["idEnlace"]),
            checkout_url=link.get("urlEnlace"),
            raw=link,
        )

    def charge_one_time(self, request: ChargeRequest) -> ChargeResult:
        """Charge a tokenized card without 3DS.

        Requires ``request.payment_method_token`` (a card token from
        client-side tokenization with the Wompi public key). Cards that demand
        3DS must go through :meth:`charge_one_time_3ds` instead.
        """
        if not request.payment_method_token:
            raise PaymentProviderError(
                "Wompi one-time charges require a tokenized card "
                "(payment_method_token) — tokenize with the Wompi JS SDK first"
            )
        email = request.metadata.get("email")
        body = {
            "monto": request.amount_cents / 100,
            "emailCliente": email or "cliente@example.com",
            "nombreCliente": request.metadata.get("customer_name") or "Cliente",
            "tokenTarjeta": request.payment_method_token,
            "configuracion": {
                # Only request notifications when we have a real address.
                **({"emailsNotificacion": email} if email else {}),
                "notificarTransaccionCliente": bool(email),
            },
            "datosAdicionales": request.metadata,
        }
        out = self._request("POST", "TransaccionCompra/TokenizadaSin3Ds", json=body)
        return ChargeResult(
            external_ref=out.get("idTransaccion"),
            status="succeeded" if out.get("esAprobada") else "failed",
            amount_cents=request.amount_cents,
            currency=request.currency,
            raw=out,
        )

    def charge_one_time_3ds(
        self,
        request: ChargeRequest,
        *,
        cvv: str,
        billing: dict,
    ) -> ChargeResult:
        """Start a 3DS one-time charge with a tokenized card.

        ``billing`` must carry the cardholder address fields the Wompi 3DS
        endpoint requires (``nombre``, ``apellido``, ``email``, ``ciudad``,
        ``direccion``, ``idPais``, ``idRegion``, ``codigoPostal``,
        ``telefono``). Returns a **pending** result whose ``raw`` carries
        ``urlCompletarPago3Ds`` — redirect the customer there to authenticate;
        the outcome arrives by webhook.
        """
        if not request.payment_method_token:
            raise PaymentProviderError(
                "Wompi 3DS charges require a tokenized card (payment_method_token)"
            )
        if not self.redirect_url:
            raise PaymentProviderError(
                "WOMPI_3DS_REDIRECT_URL must be configured for 3DS charges"
            )
        body = {
            "datosTarjetaTokenizada": {"token": request.payment_method_token, "cvv": cvv},
            "monto": request.amount_cents / 100,
            "configuracion": {},
            "urlRedirect": self.redirect_url,
            "idExterno": request.metadata.get("id_externo"),
            "datosAdicionales": request.metadata,
            **billing,
        }
        out = self._request("POST", "TransaccionCompra/3Ds", json=body)
        return ChargeResult(
            external_ref=out.get("idTransaccion"),
            status="pending",  # completes via webhook after the 3DS redirect
            amount_cents=request.amount_cents,
            currency=request.currency,
            raw=out,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _request(self, method: str, path: str, **kwargs):
        """Authenticated Wompi call; gateway errors become PaymentProviderError."""
        try:
            return self._client.request(method, path, **kwargs)
        except WompiAPIError as exc:
            raise PaymentProviderError(
                f"Wompi API error {exc.status_code}: {exc}"
            ) from exc
        except (WompiAuthError, WompiConnectionError) as exc:
            raise PaymentProviderError(f"Wompi auth/connection error: {exc}") from exc

    @classmethod
    def from_settings(cls, settings) -> "WompiPaymentProvider":
        return cls(
            client_id=settings.WOMPI_CLIENT_ID,
            client_secret=settings.WOMPI_CLIENT_SECRET,
            environment=getattr(settings, "WOMPI_ENVIRONMENT", "sandbox"),
            api_base_url=getattr(settings, "WOMPI_API_BASE_URL", _DEFAULT_API_BASE_URL),
            token_url=getattr(settings, "WOMPI_TOKEN_URL", _DEFAULT_TOKEN_URL),
            tier_price_cents=settings.SUBSCRIPTION_TIER_PRICE_CENTS,
            webhook_url=getattr(settings, "WOMPI_WEBHOOK_URL", ""),
            # ``WOMPI_REDIRECT_URL`` is the current name; the legacy
            # ``WOMPI_3DS_REDIRECT_URL`` still works as a fallback.
            redirect_url=(
                getattr(settings, "WOMPI_REDIRECT_URL", "")
                or getattr(settings, "WOMPI_3DS_REDIRECT_URL", "")
            ),
        )
