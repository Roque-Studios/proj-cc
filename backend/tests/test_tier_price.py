"""Per-creator subscription tier price tests.

A creator sets their own monthly price from the admin Settings tab
(``creator_profile.tier_price_cents``); it replaces the hardcoded $5 platform
default wherever the price is used:

- the checkout status endpoint and the public landing payload report it;
- the subscription row **snapshots** it at checkout (so renewals stay priced
  at what the subscriber agreed to pay);
- the revenue ledger records that exact amount for completed payments;
- amount-based gateways (Wompi payment links) charge it.

``None``/unset falls back to ``settings.SUBSCRIPTION_TIER_PRICE_CENTS``.
"""

from __future__ import annotations

from sqlalchemy import select

from app.config import settings
from app.models import CreatorGatewayConfig, Payment, Subscription, User
from app.payments.mock import MockPaymentProvider
from app.services.subscriptions import SubscriptionService


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


def _make_creator(client, db, email: str = "creator@example.com") -> tuple[dict, int]:
    """Register a creator via the API and enable the zero-config mock gateway."""
    headers = _register(client, email)
    resp = client.post("/creator/apply", headers=headers)
    assert resp.status_code == 200
    user = db.scalar(select(User).where(User.email == email))
    assert user is not None
    db.add(
        CreatorGatewayConfig(
            creator_id=user.id,
            gateway="mock",
            enabled=True,
            config={},
        )
    )
    db.commit()
    return headers, user.id


def _set_price(client, headers: dict, cents: int | None) -> dict:
    resp = client.put(
        "/creator/profile",
        json={"tier_price_cents": cents},
        headers=headers,
    )
    return resp.json()


def _consent_payload(creator_id: int) -> dict:
    return {
        "creator_id": creator_id,
        "accepted_tos": True,
        "age_confirmed": True,
    }


# --------------------------------------------------------------------------- #
# Admin: set / read / validate the price
# --------------------------------------------------------------------------- #


def test_profile_sets_and_reads_tier_price(client, db_session):
    headers, _creator_id = _make_creator(client, db_session)
    profile = _set_price(client, headers, 1299)
    assert profile["tier_price_cents"] == 1299

    fetched = client.get("/creator/profile", headers=headers).json()
    assert fetched["tier_price_cents"] == 1299


def test_tier_price_validation_bounds(client, db_session):
    headers, _creator_id = _make_creator(client, db_session)
    # Below the $1.00 minimum.
    resp = client.put(
        "/creator/profile", json={"tier_price_cents": 50}, headers=headers
    )
    assert resp.status_code == 422
    # Above the $10,000.00 cap.
    resp = client.put(
        "/creator/profile", json={"tier_price_cents": 1_500_000}, headers=headers
    )
    assert resp.status_code == 422


def test_tier_price_none_restores_platform_default(client, db_session):
    headers, _creator_id = _make_creator(client, db_session)
    _set_price(client, headers, 1299)
    profile = _set_price(client, headers, None)
    assert profile["tier_price_cents"] is None


# --------------------------------------------------------------------------- #
# Public exposure
# --------------------------------------------------------------------------- #


def test_landing_exposes_creator_price(client, db_session):
    headers, creator_id = _make_creator(client, db_session)
    _set_price(client, headers, 1999)
    landing = client.get(f"/creators/{creator_id}/landing").json()
    assert landing["profile"]["tier_price_cents"] == 1999

    # Unset -> the platform default.
    _set_price(client, headers, None)
    landing = client.get(f"/creators/{creator_id}/landing").json()
    assert landing["profile"]["tier_price_cents"] == settings.SUBSCRIPTION_TIER_PRICE_CENTS


# --------------------------------------------------------------------------- #
# Subscribe flow: snapshot + status display
# --------------------------------------------------------------------------- #


def test_subscribe_snapshots_creator_price(client, db_session):
    headers, creator_id = _make_creator(client, db_session)
    _set_price(client, headers, 1299)
    sub_headers = _register(client, "sub@example.com")

    resp = client.post("/subscribe", json=_consent_payload(creator_id), headers=sub_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["subscription"]["tier_price_cents"] == 1299

    with db_session as db:
        row = db.scalar(select(Subscription).where(Subscription.id == body["subscription"]["id"]))
        assert row.tier_price_cents == 1299

    status = client.get(f"/subscribe/status?creator_id={creator_id}", headers=sub_headers).json()
    assert status["tier_price_cents"] == 1299


def test_subscribe_without_price_uses_platform_default(client, db_session):
    _headers, creator_id = _make_creator(client, db_session)
    sub_headers = _register(client, "sub@example.com")

    resp = client.post("/subscribe", json=_consent_payload(creator_id), headers=sub_headers)
    assert resp.status_code == 201
    assert resp.json()["subscription"]["tier_price_cents"] == settings.SUBSCRIPTION_TIER_PRICE_CENTS

    status = client.get(f"/subscribe/status?creator_id={creator_id}", headers=sub_headers).json()
    assert status["tier_price_cents"] == settings.SUBSCRIPTION_TIER_PRICE_CENTS


# --------------------------------------------------------------------------- #
# Revenue ledger records the agreed amount
# --------------------------------------------------------------------------- #


def test_webhook_ledger_records_creator_price(client, db_session):
    headers, creator_id = _make_creator(client, db_session)
    _set_price(client, headers, 1299)
    sub_headers = _register(client, "sub@example.com")

    resp = client.post("/subscribe", json=_consent_payload(creator_id), headers=sub_headers)
    assert resp.status_code == 201
    external_ref = resp.json()["subscription"]["external_ref"]

    provider = MockPaymentProvider()
    with db_session as db:
        service = SubscriptionService(db, provider=provider)
        body = MockPaymentProvider.make_webhook_body(
            "payment.succeeded", external_ref=external_ref
        )
        service.handle_webhook(body, MockPaymentProvider.sign_body(body))

        payment = db.scalar(select(Payment).where(Payment.external_ref == external_ref))
        assert payment is not None
        assert payment.amount_cents == 1299
