"""Wompi (El Salvador) payment gateway implementation.

Wompi SV is a distinct product from Wompi Colombia: it authenticates with
**OAuth2 client credentials** (App ID / API Secret) instead of public/private
keys, and signs webhooks with the ``wompi_hash`` header — the HMAC-SHA256 of
the **raw** body keyed with the API Secret. The `pywompi` package handles the
OAuth token caching, the generic authenticated ``request(method, path, json=)``
(used for every endpoint below), and webhook validation via
``parse_event(raw_body, received_hash, api_secret)``.

Payment model (from the Wompi SV Swagger):

- **Recurring** — ``POST /EnlacePagoRecurrente`` creates a hosted recurring
  payment link (``diaDePago`` = day of month, ``monto``, ``nombre``); the
  customer subscribes on Wompi's page (3DS is handled there) and is charged
  monthly. We create **one link per subscription** so the link id doubles as
  our external ref and cancellation = disabling the link
  (``POST /EnlacePagoRecurrente/{id}``).
- **One-time** — ``POST /TransaccionCompra/TokenizadaSin3Ds`` charges a
  previously tokenized card (no 3DS); ``POST /TransaccionCompra/3Ds`` starts
  a 3DS flow returning ``urlCompletarPago3Ds`` for the customer to
  authenticate.
- **Webhooks** — transaction events carry ``data.transaccion.estado``
  (``APROBADA`` / ``RECHAZADA``). Recurring-charge events may not reference
  our link id directly, so the provider surfaces ``customer_email`` and the
  service falls back to matching by email (see ``SubscriptionService``).

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
        dia_de_pago: int = 1,
        tier_price_cents: int = 500,
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
        if not 1 <= dia_de_pago <= 31:
            raise ProviderConfigurationError(
                "WOMPI_DIA_DE_PAGO must be a day of month between 1 and 31"
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.environment = environment
        self.dia_de_pago = dia_de_pago
        self.tier_price_cents = tier_price_cents
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
        """Create a per-subscription recurring payment link.

        The customer opens ``urlEnlace`` and subscribes on Wompi's hosted page
        (3DS handled there); Wompi then charges them ``tier_price_cents`` on
        ``dia_de_pago`` each month. One link per subscription keeps the link id
        (``external_ref``) unambiguous for renewal webhooks and lets
        cancellation disable exactly that link.
        """
        subscriber_id = intent.metadata.get("subscriber_id", "?")
        creator_id = intent.metadata.get("creator_id", "?")
        link = self._request(
            "POST",
            "EnlacePagoRecurrente",
            json={
                "nombre": f"CCE-{subscriber_id}",
                "monto": self.tier_price_cents / 100,
                "diaDePago": self.dia_de_pago,
                "descripcionProducto": f"Creator subscription ({creator_id})",
            },
        )
        return SubscriptionResult(
            external_ref=link["idEnlace"],
            status="incomplete",
            checkout_url=link.get("urlEnlace"),
            raw=link,
        )

    def cancel_subscription(self, external_ref: str) -> None:
        """Disable the recurring link — Wompi stops future charges."""
        self._request("POST", f"EnlacePagoRecurrente/{external_ref}")

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
        """
        try:
            event = parse_event(
                raw_body=body,
                received_hash=headers.get("wompi_hash", ""),
                api_secret=self.client_secret,
            )
        except WompiWebhookError as exc:
            raise WebhookVerificationError(f"Invalid Wompi webhook signature: {exc}") from exc

        try:
            tx = event["data"]["transaccion"]
        except (KeyError, TypeError) as exc:
            raise WebhookVerificationError(
                "Wompi webhook payload has no transaction data"
            ) from exc

        estado = tx.get("estado", "")
        mapped = _ESTADO_MAP.get(estado)
        if mapped is None:
            raise WebhookVerificationError(f"Unhandled Wompi transaction estado: {estado}")
        event_type, subscription_status = mapped

        # A recurring-link charge event is identifiable by the subscription /
        # merchant ref (``idSuscripcion`` / ``identificadorEnlaceComercio``); a
        # plain one-time ``TransaccionCompra`` event carries only the
        # transaction id. Marking the distinction lets the service gate its
        # email fallback, so a one-time purchase event can never be reconciled
        # against a subscription (and never records a spurious monthly payment).
        recurring = bool(
            tx.get("idSuscripcion") or tx.get("identificadorEnlaceComercio")
        )

        return WebhookEvent(
            provider=self.name,
            event_type=event_type,
            # Recurring-charge events reference the suscripcion/merchant ref,
            # not the link id we stored; the service falls back to the email.
            external_ref=(
                tx.get("idSuscripcion")
                or tx.get("identificadorEnlaceComercio")
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
            },
            raw=event,
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
            dia_de_pago=getattr(settings, "WOMPI_DIA_DE_PAGO", 1),
            tier_price_cents=settings.SUBSCRIPTION_TIER_PRICE_CENTS,
            redirect_url=getattr(settings, "WOMPI_3DS_REDIRECT_URL", ""),
        )
