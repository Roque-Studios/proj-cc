"""Tests for the checkout-reconciliation endpoint ``GET /subscribe/status``.

Acceptance: the checkout UI must reconcile the final subscription state after
the hosted payment redirect — ``/subscribe/status`` returns the viewer's row
for a creator in any status (incomplete with its checkout url, active, past
due, canceled…) plus their access level, so the UI can show pending /
succeeded / failed states accurately.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Subscription, SubscriptionStatus, User


def _register(client, email: str, password: str = "StChk123"):
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201


def _login(client, email: str, password: str = "StChk123"):
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_creator(client, email: str = "stchkcr@example.com"):
    _register(client, email)
    headers = _login(client, email)
    assert client.post("/creator/apply", headers=headers).status_code == 200
    resp = client.get("/creator/profile", headers=headers)
    return headers, resp.json()["user_id"]


def _add_subscription(db, *, status, creator_id, subscriber_id, checkout_url=None):
    sub = Subscription(
        subscriber_id=subscriber_id,
        creator_id=creator_id,
        status=status,
        current_period_start=datetime.now(timezone.utc) - timedelta(days=1),
        current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
        payment_provider="mock",
        external_ref=f"sub_stchk_{subscriber_id}_{creator_id}_{status.value}",
        checkout_url=checkout_url,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def _subscriber_id(db, email: str) -> int:
    return db.scalar(select(User.id).where(User.email == email))


# --------------------------------------------------------------------------- #
# States
# --------------------------------------------------------------------------- #


def test_status_requires_auth(client, db_session):
    _, creator_id = _make_creator(client)
    assert client.get(f"/subscribe/status?creator_id={creator_id}").status_code == 401


def test_status_404_for_unknown_creator(client, db_session):
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    assert (
        client.get("/subscribe/status?creator_id=999999", headers=fan_headers).status_code
        == 404
    )


def test_status_none_for_non_subscriber(client, db_session):
    _, creator_id = _make_creator(client)
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")

    resp = client.get(f"/subscribe/status?creator_id={creator_id}", headers=fan_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["viewer_level"] == "registered"
    assert body["subscription"] is None


def test_status_reports_incomplete_with_checkout_url(client, db_session):
    """A pending payment stays visible with its hosted checkout url."""
    _, creator_id = _make_creator(client)
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        fan_id = _subscriber_id(db, "fan@example.com")
        _add_subscription(
            db,
            status=SubscriptionStatus.incomplete,
            creator_id=creator_id,
            subscriber_id=fan_id,
            checkout_url="https://mock.checkout/pending",
        )

    resp = client.get(f"/subscribe/status?creator_id={creator_id}", headers=fan_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["viewer_level"] == "registered"
    assert body["subscription"]["status"] == "incomplete"
    assert body["subscription"]["checkout_url"] == "https://mock.checkout/pending"


def test_status_reports_active_follower(client, db_session):
    _, creator_id = _make_creator(client)
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        fan_id = _subscriber_id(db, "fan@example.com")
        _add_subscription(
            db,
            status=SubscriptionStatus.active,
            creator_id=creator_id,
            subscriber_id=fan_id,
        )

    resp = client.get(f"/subscribe/status?creator_id={creator_id}", headers=fan_headers)
    body = resp.json()
    assert body["viewer_level"] == "follower"
    assert body["subscription"]["status"] == "active"


def test_status_reports_past_due_and_canceled(client, db_session):
    """Terminal/non-follower statuses still surface the row for the UI."""
    _, creator_id = _make_creator(client)
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        fan_id = _subscriber_id(db, "fan@example.com")
        _add_subscription(
            db,
            status=SubscriptionStatus.past_due,
            creator_id=creator_id,
            subscriber_id=fan_id,
        )

    resp = client.get(f"/subscribe/status?creator_id={creator_id}", headers=fan_headers)
    body = resp.json()
    assert body["viewer_level"] == "registered"
    assert body["subscription"]["status"] == "past_due"

    # A later cancellation updates the SAME row (unique subscriber+creator).
    with db_session as db:
        row = db.scalar(select(Subscription).where(Subscription.creator_id == creator_id))
        row.status = SubscriptionStatus.canceled
        db.commit()

    resp = client.get(f"/subscribe/status?creator_id={creator_id}", headers=fan_headers)
    assert resp.json()["subscription"]["status"] == "canceled"
