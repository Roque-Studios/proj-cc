"""Creator gateway-settings endpoint tests.

Covers: auth/role guards, the registry listing (per-field configured booleans,
never secret values), the enable-validation rule (a gateway with incomplete
config cannot be enabled), field-value validation (constrained environments),
the merge semantics that preserve stored secrets on partial updates, and that
secret values never leak into any response.
"""

from __future__ import annotations


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


def _register_and_apply(client, email: str) -> dict:
    """Register a user and self-serve upgrade them to creator."""
    headers = _register(client, email)
    resp = client.post("/creator/apply", headers=headers)
    assert resp.status_code == 200
    return headers


def _stripe_full_config() -> dict:
    return {
        "secret_key": "sk_live_SECRET_123",
        "webhook_secret": "whsec_SECRET_456",
    }


# --------------------------------------------------------------------------- #
# Auth + role guards
# --------------------------------------------------------------------------- #


def test_gateway_settings_requires_auth(client):
    assert client.get("/creator/gateway-settings").status_code == 401
    assert client.put("/creator/gateway-settings/stripe", json={}).status_code == 401


def test_gateway_settings_requires_creator_role(client):
    headers = _register(client, "reg@example.com")
    assert client.get("/creator/gateway-settings", headers=headers).status_code == 403
    resp = client.put(
        "/creator/gateway-settings/stripe",
        json={"enabled": True},
        headers=headers,
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# Listing
# --------------------------------------------------------------------------- #


def test_gateway_settings_listing_shape(client):
    headers = _register_and_apply(client, "creator@example.com")
    resp = client.get("/creator/gateway-settings", headers=headers)
    assert resp.status_code == 200
    gateways = {g["gateway"]: g for g in resp.json()}
    # All four creator-configurable gateways are listed in registry order.
    assert list(gateways) == ["stripe", "paypal", "wompi", "mock"]
    for gateway in ("stripe", "paypal", "wompi"):
        item = gateways[gateway]
        assert item["enabled"] is False
        # Real gateways are unconfigured until credentials are entered.
        assert item["configured"] is False
        assert item["label"]
        assert isinstance(item["fields"], list)
    # The mock gateway has no required credentials, so it is always configured.
    assert gateways["mock"]["configured"] is True
    assert gateways["mock"]["enabled"] is False


def test_wompi_has_the_three_required_fields(client):
    """Wompi enabling requires the App ID, API Secret and the Webhook URL.

    ``configuracion.urlWebhook`` is required at payment-link creation — Wompi
    only notifies transactions through it, so a paid subscription could never
    activate without it.
    """
    headers = _register_and_apply(client, "creator@example.com")
    resp = client.get("/creator/gateway-settings", headers=headers)
    wompi = next(g for g in resp.json() if g["gateway"] == "wompi")
    required = [f["name"] for f in wompi["fields"] if f["required"]]
    assert required == ["client_id", "client_secret", "webhook_url"]


# --------------------------------------------------------------------------- #
# Enable validation
# --------------------------------------------------------------------------- #


def test_enable_with_incomplete_config_rejected(client):
    headers = _register_and_apply(client, "creator@example.com")
    resp = client.put(
        "/creator/gateway-settings/stripe",
        json={"enabled": True, "config": {"secret_key": "sk_live_x"}},
        headers=headers,
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Webhook secret" in detail
    # Nothing was persisted: the gateway stays disabled.
    listing = client.get("/creator/gateway-settings", headers=headers).json()
    stripe = next(g for g in listing if g["gateway"] == "stripe")
    assert stripe["enabled"] is False


def test_enable_mock_without_config(client):
    """The mock gateway has no credentials, so it can be enabled empty."""
    headers = _register_and_apply(client, "creator@example.com")
    resp = client.put(
        "/creator/gateway-settings/mock",
        json={"enabled": True},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    assert resp.json()["configured"] is True


def test_enable_stripe_with_full_config(client):
    headers = _register_and_apply(client, "creator@example.com")
    resp = client.put(
        "/creator/gateway-settings/stripe",
        json={"enabled": True, "config": _stripe_full_config()},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["configured"] is True
    by_name = {f["name"]: f for f in body["fields"]}
    assert by_name["secret_key"]["configured"] is True
    assert by_name["webhook_secret"]["configured"] is True
    assert by_name["api_base"]["configured"] is False


def test_enable_wompi_with_three_variables(client):
    """Wompi enables with App ID + API Secret + Webhook URL."""
    headers = _register_and_apply(client, "creator@example.com")
    resp = client.put(
        "/creator/gateway-settings/wompi",
        json={
            "enabled": True,
            "config": {
                "client_id": "app_abc",
                "client_secret": "secret_xyz",
                "webhook_url": "https://example.com/api/webhooks/wompi",
            },
        },
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    assert resp.json()["configured"] is True


def test_unknown_gateway_404(client):
    headers = _register_and_apply(client, "creator@example.com")
    resp = client.put(
        "/creator/gateway-settings/bitcoin",
        json={"enabled": True},
        headers=headers,
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Field-value validation
# --------------------------------------------------------------------------- #


def test_paypal_environment_must_be_sandbox_or_live(client):
    headers = _register_and_apply(client, "creator@example.com")
    resp = client.put(
        "/creator/gateway-settings/paypal",
        json={
            "enabled": True,
            "config": {
                "client_id": "cid",
                "client_secret": "csec",
                "webhook_id": "whid",
                "environment": "banana",
            },
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert "sandbox" in resp.json()["detail"]


def test_wompi_environment_validated(client):
    headers = _register_and_apply(client, "creator@example.com")
    bad_env = client.put(
        "/creator/gateway-settings/wompi",
        json={
            "enabled": True,
            "config": {
                "client_id": "a",
                "client_secret": "b",
                "webhook_url": "https://example.com/api/webhooks/wompi",
                "environment": "moon",
            },
        },
        headers=headers,
    )
    assert bad_env.status_code == 400


def test_non_secret_values_are_echoed_and_secrets_never(client):
    """The form can pre-fill non-secret fields; secret values stay hidden.

    ``value`` echoes stored **non-secret** config (e.g. the environment
    select) so a save never silently resets it; secret keys always come back
    ``None`` — only the ``configured`` boolean reveals they exist.
    """
    headers = _register_and_apply(client, "creator@example.com")
    saved = client.put(
        "/creator/gateway-settings/wompi",
        json={
            "enabled": True,
            "config": {
                "client_id": "app_live_1",
                "client_secret": "secret_live_1",
                "webhook_url": "https://example.com/api/webhooks/wompi",
                "environment": "production",
            },
        },
        headers=headers,
    )
    assert saved.status_code == 200

    resp = client.get("/creator/gateway-settings", headers=headers)
    wompi = next(g for g in resp.json() if g["gateway"] == "wompi")
    fields = {f["name"]: f for f in wompi["fields"]}
    # Non-secret stored value is echoed (select pre-fill).
    assert fields["environment"]["value"] == "production"
    # Secrets never leak — value is None, only the configured boolean.
    assert fields["client_id"]["value"] is None
    assert fields["client_secret"]["value"] is None
    assert fields["client_id"]["configured"] is True
    assert fields["client_secret"]["configured"] is True


# --------------------------------------------------------------------------- #
# Disable + partial updates preserve secrets
# --------------------------------------------------------------------------- #


def test_disable_keeps_config_for_later_reenable(client):
    headers = _register_and_apply(client, "creator@example.com")
    enable = client.put(
        "/creator/gateway-settings/stripe",
        json={"enabled": True, "config": _stripe_full_config()},
        headers=headers,
    )
    assert enable.status_code == 200
    disable = client.put(
        "/creator/gateway-settings/stripe",
        json={"enabled": False},
        headers=headers,
    )
    assert disable.status_code == 200
    assert disable.json()["enabled"] is False
    assert disable.json()["configured"] is True  # creds retained


def test_partial_update_keeps_existing_secrets(client):
    headers = _register_and_apply(client, "creator@example.com")
    client.put(
        "/creator/gateway-settings/stripe",
        json={"enabled": False, "config": _stripe_full_config()},
        headers=headers,
    )
    # Update only the api_base; the client cannot see the stored secrets, so
    # omitting them must not wipe them.
    resp = client.put(
        "/creator/gateway-settings/stripe",
        json={"config": {"api_base": "https://api.stripe.com/v1"}},
        headers=headers,
    )
    assert resp.status_code == 200
    by_name = {f["name"]: f for f in resp.json()["fields"]}
    assert by_name["secret_key"]["configured"] is True
    assert by_name["webhook_secret"]["configured"] is True
    assert by_name["api_base"]["configured"] is True


# --------------------------------------------------------------------------- #
# Secrets never leave the API
# --------------------------------------------------------------------------- #


def test_secret_values_never_echoed(client):
    headers = _register_and_apply(client, "creator@example.com")
    secret_key = "sk_live_TOP_SECRET_abc123"
    webhook_secret = "whsec_TOP_SECRET_xyz789"
    put = client.put(
        "/creator/gateway-settings/stripe",
        json={
            "enabled": True,
            "config": {
                "secret_key": secret_key,
                "webhook_secret": webhook_secret,
            },
        },
        headers=headers,
    )
    assert put.status_code == 200
    assert secret_key not in put.text
    assert webhook_secret not in put.text

    listing = client.get("/creator/gateway-settings", headers=headers)
    assert secret_key not in listing.text
    assert webhook_secret not in listing.text
    stripe = next(g for g in listing.json() if g["gateway"] == "stripe")
    assert stripe["configured"] is True
    by_name = {f["name"]: f for f in stripe["fields"]}
    assert by_name["secret_key"]["configured"] is True


def test_settings_are_scoped_to_own_creator(client):
    first = _register_and_apply(client, "creator-a@example.com")
    client.put(
        "/creator/gateway-settings/stripe",
        json={"enabled": True, "config": _stripe_full_config()},
        headers=first,
    )
    second = _register_and_apply(client, "creator-b@example.com")
    listing = client.get("/creator/gateway-settings", headers=second).json()
    stripe = next(g for g in listing if g["gateway"] == "stripe")
    assert stripe["enabled"] is False
    assert stripe["configured"] is False
