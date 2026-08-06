"""Per-creator webhook credential matching tests.

With strictly per-creator credentials, provider webhooks are signed with the
*creator's* gateway secret, so the webhook router must try every registered
credential set for the gateway (platform env first, then each creator's stored
config). Covers: a Stripe event signed with the creator's webhook secret
reconciles the right subscription; a forged signature fails with 400; and the
platform-env mock path still verifies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

from sqlalchemy import select

from app.models import Subscription, SubscriptionStatus, User, UserRole


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


def _signed_webhook(webhook_secret: str, payload: dict) -> tuple[bytes, dict]:
    """Sign a body the way Stripe does (t=...,v1=...) with a given secret."""
    body = json.dumps(payload).encode()
    timestamp = int(time.time())
    signed = f"{timestamp}.".encode() + body
    sig = hmac.new(
        webhook_secret.encode(), signed, hashlib.sha256
    ).hexdigest()
    return body, {"stripe-signature": f"t={timestamp},v1={sig}"}


def _stripe_invoice_paid(external_ref: str = "sub_creator_1") -> dict:
    return {
        "id": "evt_per_creator_1",
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": "in_1",
                "subscription": external_ref,
                "period_start": 1760000000,
                "period_end": 1762592000,
                "status": "active",
                # Real Stripe invoices carry payment_intent; the reconcilable
                # ref must stay the subscription id.
                "payment_intent": "pi_1",
            }
        },
    }


def _enable_stripe(client, headers: dict, webhook_secret: str) -> None:
    resp = client.put(
        "/creator/gateway-settings/stripe",
        json={
            "enabled": True,
            "config": {
                "secret_key": "sk_live_creator",
                "webhook_secret": webhook_secret,
            },
        },
        headers=headers,
    )
    assert resp.status_code == 200


def test_webhook_validates_with_per_creator_secret(client, db_session):
    """A Stripe event signed with the creator's own secret reconciles."""
    headers = _register(client, "creator@example.com")
    assert client.post("/creator/apply", headers=headers).status_code == 200
    creator_secret = "whsec_creator_only"
    _enable_stripe(client, headers, creator_secret)

    with db_session as db:
        creator = db.scalar(select(User).where(User.email == "creator@example.com"))
        subscriber = User(
            email="sub@example.com",
            username="sub",
            hashed_password="x",
            role=UserRole.registered,
            is_active=True,
        )
        db.add(subscriber)
        db.commit()
        db.refresh(subscriber)
        sub = Subscription(
            subscriber_id=subscriber.id,
            creator_id=creator.id,
            status=SubscriptionStatus.incomplete,
            payment_provider="stripe",
            external_ref="sub_creator_1",
            checkout_url="https://checkout.stripe.com/cs_test_1",
        )
        db.add(sub)
        db.commit()
        sub_id = sub.id

    body, sig_headers = _signed_webhook(creator_secret, _stripe_invoice_paid())
    resp = client.post("/webhooks/stripe", content=body, headers=sig_headers)
    assert resp.status_code == 200
    assert resp.json()["event_type"] == "payment.succeeded"

    with db_session as db:
        row = db.get(Subscription, sub_id)
        assert row.status == SubscriptionStatus.active
        assert row.checkout_url is None


def test_webhook_forged_signature_rejected_even_with_creator_config(client, db_session):
    """A wrong secret fails every candidate and answers 400 (never 500)."""
    headers = _register(client, "creator@example.com")
    assert client.post("/creator/apply", headers=headers).status_code == 200
    _enable_stripe(client, headers, "whsec_creator_only")

    body, sig_headers = _signed_webhook("whsec_forged", _stripe_invoice_paid())
    resp = client.post("/webhooks/stripe", content=body, headers=sig_headers)
    assert resp.status_code == 400
    assert "verification failed" in resp.json()["detail"]


def test_webhook_mock_platform_path_still_verifies(client, db_session):
    """The env-driven mock path is unchanged: no creator config needed."""
    headers = _register(client, "creator@example.com")
    assert client.post("/creator/apply", headers=headers).status_code == 200
    from app.payments.mock import MockPaymentProvider

    with db_session as db:
        creator = db.scalar(select(User).where(User.email == "creator@example.com"))
        subscriber = User(
            email="sub2@example.com",
            username="sub2",
            hashed_password="x",
            role=UserRole.registered,
            is_active=True,
        )
        db.add(subscriber)
        db.commit()
        db.refresh(subscriber)
        sub = Subscription(
            subscriber_id=subscriber.id,
            creator_id=creator.id,
            status=SubscriptionStatus.incomplete,
            payment_provider="mock",
            external_ref="sub_mock_webhook",
        )
        db.add(sub)
        db.commit()
        sub_id = sub.id

    body = MockPaymentProvider.make_webhook_body(
        "payment.succeeded", external_ref="sub_mock_webhook", event_id="evt_mock_1"
    )
    resp = client.post(
        "/webhooks/mock", content=body, headers=MockPaymentProvider.sign_body(body)
    )
    assert resp.status_code == 200
    with db_session as db:
        assert db.get(Subscription, sub_id).status == SubscriptionStatus.active
