"""Checkout gateway listing + per-creator subscribe resolution tests.

Covers the "subscriber checkout only shows enabled gateways" acceptance:
``GET /creators/{id}/gateways`` returns only enabled + configured gateways, and
``POST /subscribe`` resolves the gateway strictly from the creator's config
(never platform env): explicit provider validation, no-gateway and
ambiguous-multiple errors, and single-gateway defaulting.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.payments.base import SubscriptionResult


def _register(client, email: str, password: str = "Passw0rd1") -> dict:
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201
    token = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _make_creator(client, email: str) -> tuple[dict, int]:
    """Register + apply a creator via the API; returns (headers, creator_id)."""
    headers = _register(client, email)
    resp = client.post("/creator/apply", headers=headers)
    assert resp.status_code == 200
    return headers, resp.json()["user_id"]


def _configure(client, headers: dict, gateway: str, enabled: bool, config: dict):
    resp = client.put(
        f"/creator/gateway-settings/{gateway}",
        json={"enabled": enabled, "config": config},
        headers=headers,
    )
    assert resp.status_code == 200
    return resp.json()


class _StubProvider:
    """Deterministic stand-in for the real gateway client (no network)."""

    def __init__(self, gateway: str, config: dict):
        self.gateway = gateway
        self.name = gateway  # stored as the row's payment_provider
        self.config = config

    def create_customer(self, email, name=None, metadata=None):
        return f"cus_{email}"

    def create_subscription(self, intent):
        now = datetime.now(timezone.utc)
        return SubscriptionResult(
            external_ref=f"sub_{self.gateway}_1",
            status="active",
            checkout_url=f"https://stub.checkout/{self.gateway}",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )


@pytest.fixture
def stub_build(monkeypatch):
    """Swap the factory so subscribe uses a recording stub provider."""
    calls = []

    def fake_build(gateway: str, config: dict):
        calls.append((gateway, dict(config)))
        return _StubProvider(gateway, config)

    monkeypatch.setattr("app.routers.subscriptions.build_provider_from_config", fake_build)
    return calls


# --------------------------------------------------------------------------- #
# Checkout listing
# --------------------------------------------------------------------------- #


def test_checkout_gateways_empty_without_config(client):
    _headers, creator_id = _make_creator(client, "creator@example.com")
    resp = client.get(f"/creators/{creator_id}/gateways")
    assert resp.status_code == 200
    assert resp.json() == []


def test_checkout_gateways_unknown_creator_404(client):
    assert client.get("/creators/999999/gateways").status_code == 404


def test_checkout_lists_only_enabled_configured_gateways(client):
    headers, creator_id = _make_creator(client, "creator@example.com")
    _configure(
        client, headers, "stripe",
        enabled=True,
        config={"secret_key": "sk_live_x", "webhook_secret": "whsec_x"},
    )
    _configure(
        client, headers, "wompi",
        enabled=True,
        config={
            "client_id": "a",
            "client_secret": "b",
            "webhook_url": "https://example.com/api/webhooks/wompi",
        },
    )
    # A disabled gateway with full config must NOT appear.
    _configure(
        client, headers, "paypal",
        enabled=False,
        config={"client_id": "c", "client_secret": "d", "webhook_id": "e"},
    )
    listed = client.get(f"/creators/{creator_id}/gateways").json()
    assert [g["gateway"] for g in listed] == ["stripe", "wompi"]

    # Disabling removes the gateway from checkout.
    _configure(client, headers, "stripe", enabled=False, config={})
    listed = client.get(f"/creators/{creator_id}/gateways").json()
    assert [g["gateway"] for g in listed] == ["wompi"]


def test_checkout_excludes_incomplete_config(client):
    headers, creator_id = _make_creator(client, "creator@example.com")
    # An incomplete stripe row can't be enabled, so it never reaches checkout.
    _configure(client, headers, "stripe", enabled=False, config={"secret_key": "sk_live_x"})
    listed = client.get(f"/creators/{creator_id}/gateways").json()
    assert listed == []


# --------------------------------------------------------------------------- #
# Subscribe provider resolution
# --------------------------------------------------------------------------- #


def test_subscribe_uses_explicit_enabled_gateway(client, stub_build):
    headers, creator_id = _make_creator(client, "creator@example.com")
    _configure(
        client, headers, "stripe",
        enabled=True,
        config={"secret_key": "sk_live_x", "webhook_secret": "whsec_x"},
    )
    sub_headers = _register(client, "sub@example.com")
    resp = client.post(
        "/subscribe",
        json={
            "creator_id": creator_id,
            "provider": "stripe",
            "accepted_tos": True,
            "age_confirmed": True,
        },
        headers=sub_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["subscription"]["payment_provider"] == "stripe"
    assert body["checkout_url"] == "https://stub.checkout/stripe"
    # The factory received the creator's stored config.
    assert stub_build == [("stripe", {"secret_key": "sk_live_x", "webhook_secret": "whsec_x"})]


def test_subscribe_explicit_gateway_not_enabled_400(client, stub_build):
    headers, creator_id = _make_creator(client, "creator@example.com")
    _configure(
        client, headers, "stripe",
        enabled=False,
        config={"secret_key": "sk_live_x", "webhook_secret": "whsec_x"},
    )
    sub_headers = _register(client, "sub@example.com")
    resp = client.post(
        "/subscribe",
        json={"creator_id": creator_id, "provider": "stripe"},
        headers=sub_headers,
    )
    assert resp.status_code == 400
    assert "not enabled" in resp.json()["detail"]
    assert stub_build == []


def test_subscribe_explicit_unknown_gateway_400(client):
    headers, creator_id = _make_creator(client, "creator@example.com")
    sub_headers = _register(client, "sub@example.com")
    resp = client.post(
        "/subscribe",
        json={"creator_id": creator_id, "provider": "bitcoin"},
        headers=sub_headers,
    )
    assert resp.status_code == 400


def test_subscribe_without_provider_and_none_enabled_400(client, stub_build):
    headers, creator_id = _make_creator(client, "creator@example.com")
    sub_headers = _register(client, "sub@example.com")
    resp = client.post(
        "/subscribe",
        json={"creator_id": creator_id, "accepted_tos": True, "age_confirmed": True},
        headers=sub_headers,
    )
    assert resp.status_code == 400
    assert "no payment gateway" in resp.json()["detail"].lower()
    assert stub_build == []


def test_subscribe_without_provider_and_multiple_enabled_400(client, stub_build):
    headers, creator_id = _make_creator(client, "creator@example.com")
    _configure(
        client, headers, "stripe",
        enabled=True,
        config={"secret_key": "sk_live_x", "webhook_secret": "whsec_x"},
    )
    _configure(
        client, headers, "wompi",
        enabled=True,
        config={
            "client_id": "a",
            "client_secret": "b",
            "webhook_url": "https://example.com/api/webhooks/wompi",
        },
    )
    sub_headers = _register(client, "sub@example.com")
    resp = client.post(
        "/subscribe",
        json={"creator_id": creator_id},
        headers=sub_headers,
    )
    assert resp.status_code == 400
    assert "multiple" in resp.json()["detail"].lower()
    assert stub_build == []


def test_subscribe_without_provider_defaults_to_single_enabled(client, stub_build):
    headers, creator_id = _make_creator(client, "creator@example.com")
    _configure(
        client, headers, "stripe",
        enabled=True,
        config={"secret_key": "sk_live_x", "webhook_secret": "whsec_x"},
    )
    sub_headers = _register(client, "sub@example.com")
    resp = client.post(
        "/subscribe",
        json={"creator_id": creator_id, "accepted_tos": True, "age_confirmed": True},
        headers=sub_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["subscription"]["payment_provider"] == "stripe"
    assert stub_build == [("stripe", {"secret_key": "sk_live_x", "webhook_secret": "whsec_x"})]


# --------------------------------------------------------------------------- #
# Factory: per-creator config -> provider / plan
# --------------------------------------------------------------------------- #


def test_build_provider_from_config_maps_stored_fields():
    """A creator's stored config builds a provider carrying those credentials."""
    from app.payments.factory import build_provider_from_config

    provider = build_provider_from_config(
        "stripe",
        {
            "secret_key": "sk_live_creator",
            "webhook_secret": "whsec_creator",
            "api_base": "https://example.invalid/v1",
        },
    )
    assert provider.name == "stripe"
    assert provider.secret_key == "sk_live_creator"
    assert provider.webhook_secret == "whsec_creator"


def test_build_provider_from_config_ignores_legacy_keys():
    """Stale config keys (e.g. the removed dia_de_pago) don't break the build."""
    from app.payments.factory import build_provider_from_config

    provider = build_provider_from_config(
        "wompi",
        {
            "client_id": "app_1",
            "client_secret": "secret_1",
            "dia_de_pago": "5",  # legacy key from the old recurring-link config
            "webhook_url": "https://example.com/api/webhooks/wompi",
            "redirect_url": "https://example.com/return",
        },
    )
    assert provider.name == "wompi"
    assert provider.webhook_url == "https://example.com/api/webhooks/wompi"
    assert provider.redirect_url == "https://example.com/return"


def test_resolve_plan_id_prefers_creator_plan_then_env():
    from app.config import settings
    from app.payments.factory import resolve_plan_id

    # A creator-pinned plan wins when it looks like the gateway's own id.
    assert resolve_plan_id("paypal", {"plan_id": "P-ABC123"}) == "P-ABC123"
    assert resolve_plan_id("stripe", {"plan_id": "price_monthly_1"}) == "price_monthly_1"
    # Gateways that don't send a plan id (Wompi payment links) skip validation.
    assert resolve_plan_id("wompi", {"plan_id": "   "}) == settings.SUBSCRIPTION_TIER_PLAN_ID


def test_resolve_plan_id_rejects_non_paypal_plan_id():
    """A plan id that can't be a PayPal billing plan fails fast with a clear error.

    Regression guard: the platform default ``SUBSCRIPTION_TIER_PLAN_ID`` is the
    Stripe placeholder ``price_monthly_tier``; without this check it was sent to
    PayPal, which rejected it with a cryptic ``INVALID_REQUEST`` 400 (PayPal plan
    ids look like ``P-...``).
    """
    from app.payments import ProviderConfigurationError
    from app.payments.factory import resolve_plan_id

    # No creator plan -> falls back to the env default, which isn't a PayPal id.
    with pytest.raises(ProviderConfigurationError, match="P-"):
        resolve_plan_id("paypal", {})
    # A creator-pinned plan that isn't a PayPal id is rejected too.
    with pytest.raises(ProviderConfigurationError, match="price_monthly_tier"):
        resolve_plan_id("paypal", {"plan_id": "price_monthly_tier"})


def test_resolve_plan_id_rejects_non_stripe_plan_id():
    from app.payments import ProviderConfigurationError
    from app.payments.factory import resolve_plan_id

    with pytest.raises(ProviderConfigurationError, match="price_"):
        resolve_plan_id("stripe", {"plan_id": "P-ABC123"})


def test_subscribe_paypal_without_plan_id_generic_502(client, stub_build):
    """A creator's PayPal gateway with no plan id -> generic 502, no leak.

    The checkout must never send a foreign plan id (the Stripe placeholder
    default) to PayPal — and the operator-facing reason (billing-plan setup)
    must never reach the subscriber: they get a generic "payment method
    temporarily unavailable" message, the technical detail only goes to the
    server log.
    """
    headers, creator_id = _make_creator(client, "creator@example.com")
    _configure(
        client, headers, "paypal",
        enabled=True,
        config={"client_id": "c", "client_secret": "d", "webhook_id": "e"},
    )
    sub_headers = _register(client, "sub@example.com")
    resp = client.post(
        "/subscribe",
        json={
            "creator_id": creator_id,
            "provider": "paypal",
            "accepted_tos": True,
            "age_confirmed": True,
        },
        headers=sub_headers,
    )
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "temporarily unavailable" in detail.lower()
    # No operator-facing internals (bootstrap instructions, plan-id shapes).
    assert "billing plan" not in detail.lower()
    assert "P-" not in detail
    assert "bootstrap" not in detail.lower()
    assert stub_build == [("paypal", {"client_id": "c", "client_secret": "d", "webhook_id": "e"})]
