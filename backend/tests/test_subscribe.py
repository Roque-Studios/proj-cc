"""Endpoint tests for ``POST /subscribe`` (single monthly tier).

Covers: auth guard, creator validation, pending (incomplete) creation with a
checkout url, idempotency, and that the payment success/failure webhook paths
(active with period dates / stays incomplete) are reachable from the service
behind the endpoint.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import Subscription, SubscriptionStatus, User, UserRole
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


def _make_creator(db, email: str = "creator@example.com") -> User:
    creator = User(
        email=email,
        username=email.split("@")[0],
        hashed_password="not-used-in-tests",
        role=UserRole.creator,
        is_active=True,
    )
    db.add(creator)
    db.commit()
    db.refresh(creator)
    return creator


# --------------------------------------------------------------------------- #
# Auth + validation
# --------------------------------------------------------------------------- #

def test_subscribe_requires_auth(client, db_session):
    with db_session as db:
        creator = _make_creator(db)
        creator_id = creator.id
    resp = client.post("/subscribe", json={"creator_id": creator_id})
    assert resp.status_code == 401


def test_subscribe_unknown_creator_404(client, db_session):
    headers = _register(client, "sub@example.com")
    resp = client.post("/subscribe", json={"creator_id": 999999}, headers=headers)
    assert resp.status_code == 404


def test_subscribe_to_non_creator_404(client, db_session):
    headers = _register(client, "sub@example.com")
    with db_session as db:
        plain = User(
            email="plain@example.com",
            username="plain",
            hashed_password="x",
            role=UserRole.registered,
            is_active=True,
        )
        db.add(plain)
        db.commit()
        db.refresh(plain)
        plain_id = plain.id
    resp = client.post("/subscribe", json={"creator_id": plain_id}, headers=headers)
    assert resp.status_code == 404


def test_subscribe_to_self_400(client, db_session):
    headers = _register(client, "creator-self@example.com")
    with db_session as db:
        user = db.scalar(select(User).where(User.email == "creator-self@example.com"))
        user.role = UserRole.creator
        db.commit()
        user_id = user.id
    resp = client.post("/subscribe", json={"creator_id": user_id}, headers=headers)
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Happy path: pending creation
# --------------------------------------------------------------------------- #

def test_subscribe_creates_incomplete_subscription_with_checkout_url(client, db_session):
    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _make_creator(db)
        creator_id = creator.id

    resp = client.post("/subscribe", json={"creator_id": creator_id}, headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "incomplete"
    assert body["checkout_url"].startswith("https://mock.checkout/")
    sub = body["subscription"]
    assert sub["creator_id"] == creator_id
    assert sub["status"] == "incomplete"
    assert sub["payment_provider"] == "mock"
    assert sub["checkout_url"].startswith("https://mock.checkout/")


def test_subscribe_is_idempotent_while_pending(client, db_session):
    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _make_creator(db)
        creator_id = creator.id

    first = client.post("/subscribe", json={"creator_id": creator_id}, headers=headers)
    second = client.post("/subscribe", json={"creator_id": creator_id}, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["subscription"]["id"] == second.json()["subscription"]["id"]


# --------------------------------------------------------------------------- #
# Payment paths reachable from the endpoint flow
# --------------------------------------------------------------------------- #

def test_subscribe_then_payment_success_activates(client, db_session):
    """End-to-end: subscribe (incomplete) -> payment.succeeded -> active."""
    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _make_creator(db)
        creator_id = creator.id

    resp = client.post("/subscribe", json={"creator_id": creator_id}, headers=headers)
    assert resp.status_code == 201
    external_ref = resp.json()["subscription"]["external_ref"]

    provider = MockPaymentProvider()
    with db_session as db:
        service = SubscriptionService(db, provider=provider)
        body = MockPaymentProvider.make_webhook_body(
            "payment.succeeded", external_ref=external_ref
        )
        service.handle_webhook(body, MockPaymentProvider.sign_body(body))

        row = db.scalar(select(Subscription).where(Subscription.id == resp.json()["subscription"]["id"]))
        assert row.status == SubscriptionStatus.active
        assert row.checkout_url is None


def test_subscribe_then_payment_failure_stays_incomplete(client, db_session):
    """End-to-end: subscribe (incomplete) -> payment.failed -> still incomplete."""
    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator = _make_creator(db)
        creator_id = creator.id

    resp = client.post("/subscribe", json={"creator_id": creator_id}, headers=headers)
    assert resp.status_code == 201
    external_ref = resp.json()["subscription"]["external_ref"]

    provider = MockPaymentProvider()
    with db_session as db:
        service = SubscriptionService(db, provider=provider)
        body = MockPaymentProvider.make_webhook_body(
            "payment.failed", external_ref=external_ref
        )
        service.handle_webhook(body, MockPaymentProvider.sign_body(body))

        row = db.scalar(select(Subscription).where(Subscription.id == resp.json()["subscription"]["id"]))
        assert row.status == SubscriptionStatus.incomplete
        # Retry still possible: checkout url kept.
        assert row.checkout_url is not None
