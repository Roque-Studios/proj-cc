"""Tests for the creator subscriber-management view.

Acceptance: a creator sees their subscribers (paginated, filterable by status)
with subscription start dates and a revenue summary; **revenue totals match
the sum of completed payments in the DB** (the ``payment`` ledger — monthly
subscription payments + one-time unlocks, refunds excluded); access is
restricted to the owning creator (401 anonymous, 403 registered users).
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from PIL import Image
from sqlalchemy import func, select

from app.config import settings
from app.models import Payment, Post, Subscription, SubscriptionStatus, User, UserRole
from app.payments.mock import MockPaymentProvider

NOW = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)

TIER_PRICE = settings.SUBSCRIPTION_TIER_PRICE_CENTS


def _real_jpeg(width: int = 320, height: int = 240) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (70, 130, 200)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, email: str, password: str = "SubsCr123"):
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201


def _login(client, email: str, password: str = "SubsCr123") -> str:
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _make_creator(client, email: str = "cr@example.com") -> str:
    """Register + apply as a creator; returns the access token."""
    _register(client, email)
    token = _login(client, email)
    assert client.post("/creator/apply", headers=_bearer(token)).status_code == 200
    return token


def _make_user(db, email: str, *, role: UserRole = UserRole.registered) -> User:
    user = User(
        email=email,
        username=email.split("@")[0],
        hashed_password="x",
        role=role,
        is_creator=(role == UserRole.creator),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _user_id(db, email: str) -> int:
    return db.scalar(select(User.id).where(User.email == email))


def _subscribe(
    db,
    subscriber_id: int,
    creator_id: int,
    *,
    status: SubscriptionStatus = SubscriptionStatus.active,
    ref: str | None = None,
    provider: str = "mock",
    days: int = 30,
) -> Subscription:
    sub = Subscription(
        subscriber_id=subscriber_id,
        creator_id=creator_id,
        status=status,
        current_period_start=NOW - timedelta(days=1),
        current_period_end=NOW + timedelta(days=days),
        payment_provider=provider,
        external_ref=ref or f"sub_subs_{subscriber_id}_{creator_id}",
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def _add_payment(
    db,
    creator_id: int,
    subscriber_id: int,
    *,
    kind: str,
    amount_cents: int,
    status: str = "completed",
    ref: str | None = None,
    post_id: int | None = None,
) -> Payment:
    row = Payment(
        creator_id=creator_id,
        subscriber_id=subscriber_id,
        kind=kind,
        amount_cents=amount_cents,
        status=status,
        payment_provider="mock",
        external_ref=ref or f"pay_subs_{kind}_{creator_id}_{subscriber_id}_{amount_cents}",
        post_id=post_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _db_completed_sum(db, creator_id: int) -> int:
    """The sum of completed payments in the DB, computed independently."""
    return (
        db.scalar(
            select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
                Payment.creator_id == creator_id,
                Payment.status == "completed",
            )
        )
        or 0
    )


def _refund_webhook(external_ref: str, metadata: dict, event_id: str) -> tuple[bytes, dict]:
    body = MockPaymentProvider.make_webhook_body(
        "payment.refunded",
        external_ref=external_ref,
        metadata=metadata,
        event_id=event_id,
    )
    headers = MockPaymentProvider.sign_body(body)
    headers["Content-Type"] = "application/json"
    return body, headers


# --------------------------------------------------------------------------- #
# Access (acceptance: owning creator only)
# --------------------------------------------------------------------------- #


def test_subscribers_requires_auth(client):
    assert client.get("/creator/subscribers").status_code == 401


def test_registered_user_cannot_access(client, db_session):
    token = _make_creator(client)
    _register(client, "fan@example.com")
    fan_token = _login(client, "fan@example.com")
    with db_session as db:
        creator = db.get(User, _user_id(db, "cr@example.com"))
        fan = db.get(User, _user_id(db, "fan@example.com"))
        _subscribe(db, fan.id, creator.id)

    assert (
        client.get("/creator/subscribers", headers=_bearer(fan_token)).status_code
        == 403
    )
    # The creator sees their own list.
    assert (
        client.get("/creator/subscribers", headers=_bearer(token)).status_code
        == 200
    )


def test_lists_only_own_subscriptions(client, db_session):
    token_a = _make_creator(client, "a@example.com")
    _make_creator(client, "b@example.com")
    with db_session as db:
        creator_a = db.get(User, _user_id(db, "a@example.com"))
        creator_b = db.get(User, _user_id(db, "b@example.com"))
        fan1 = _make_user(db, "fan1@example.com")
        fan2 = _make_user(db, "fan2@example.com")
        sub_a = _subscribe(db, fan1.id, creator_a.id)
        _subscribe(db, fan2.id, creator_b.id)
        fan1_id = fan1.id  # read before the session closes (commit expires instances)
        sub_a_id = sub_a.id

    body = client.get("/creator/subscribers", headers=_bearer(token_a)).json()
    assert [s["subscriber_id"] for s in body["items"]] == [fan1_id]
    assert body["items"][0]["subscription_id"] == sub_a_id
    assert body["total"] == 1


# --------------------------------------------------------------------------- #
# Listing: fields, pagination, status filter
# --------------------------------------------------------------------------- #


def test_subscriber_fields(client, db_session):
    token = _make_creator(client)
    with db_session as db:
        creator = db.get(User, _user_id(db, "cr@example.com"))
        fan = _make_user(db, "fan@example.com")
        sub = _subscribe(
            db,
            fan.id,
            creator.id,
            status=SubscriptionStatus.active,
            ref="sub_fields_1",
        )
        sub_id = sub.id
        fan_id = fan.id  # read before the session closes

    body = client.get("/creator/subscribers", headers=_bearer(token)).json()
    item = body["items"][0]
    assert item["subscription_id"] == sub_id
    assert item["subscriber_id"] == fan_id
    assert item["subscriber_email"] == "fan@example.com"
    assert item["subscriber_username"] == "fan"
    assert item["status"] == "active"
    assert item["started_at"] is not None  # subscription start date
    assert item["current_period_end"] is not None
    assert item["cancel_at_period_end"] is False
    assert item["payment_provider"] == "mock"


def test_pagination_and_has_more(client, db_session):
    token = _make_creator(client)
    with db_session as db:
        creator = db.get(User, _user_id(db, "cr@example.com"))
        ids = []
        for i in range(5):
            fan = _make_user(db, f"fan{i}@example.com")
            ids.append(_subscribe(db, fan.id, creator.id, ref=f"sub_page_{i}").id)
    # Newest first (id desc tiebreak).
    expected_order = list(reversed(ids))

    page1 = client.get(
        "/creator/subscribers?page=1&page_size=2", headers=_bearer(token)
    ).json()
    assert page1["total"] == 5
    assert page1["has_more"] is True
    assert [s["subscription_id"] for s in page1["items"]] == expected_order[:2]

    page3 = client.get(
        "/creator/subscribers?page=3&page_size=2", headers=_bearer(token)
    ).json()
    assert page3["has_more"] is False
    assert [s["subscription_id"] for s in page3["items"]] == expected_order[4:]

    assert (
        client.get("/creator/subscribers?page=0", headers=_bearer(token)).status_code
        == 422
    )
    assert (
        client.get("/creator/subscribers?page_size=100", headers=_bearer(token)).status_code
        == 422
    )


def test_status_filter(client, db_session):
    token = _make_creator(client)
    with db_session as db:
        creator = db.get(User, _user_id(db, "cr@example.com"))
        fans = [_make_user(db, f"sfan{i}@example.com") for i in range(4)]
        _subscribe(db, fans[0].id, creator.id, status=SubscriptionStatus.active, ref="s_active")
        _subscribe(db, fans[1].id, creator.id, status=SubscriptionStatus.canceled, ref="s_canceled")
        _subscribe(db, fans[2].id, creator.id, status=SubscriptionStatus.past_due, ref="s_pastdue")
        _subscribe(db, fans[3].id, creator.id, status=SubscriptionStatus.trialing, ref="s_trial")

    active = client.get(
        "/creator/subscribers?status=active", headers=_bearer(token)
    ).json()
    assert [s["status"] for s in active["items"]] == ["active"]
    assert active["total"] == 1

    canceled = client.get(
        "/creator/subscribers?status=canceled", headers=_bearer(token)
    ).json()
    assert [s["status"] for s in canceled["items"]] == ["canceled"]
    assert canceled["total"] == 1

    all_items = client.get("/creator/subscribers", headers=_bearer(token)).json()
    assert all_items["total"] == 4

    bad = client.get("/creator/subscribers?status=bogus", headers=_bearer(token))
    assert bad.status_code == 400
    assert "Unknown subscription status" in bad.json()["detail"]


# --------------------------------------------------------------------------- #
# Revenue summary == sum of completed payments in the DB
# --------------------------------------------------------------------------- #


def test_revenue_matches_sum_of_completed_payments(client, db_session):
    """Acceptance: totals equal the sum of completed payments — refunds excluded."""
    token = _make_creator(client)
    with db_session as db:
        creator = db.get(User, _user_id(db, "cr@example.com"))
        fan = _make_user(db, "fan@example.com")
        _add_payment(db, creator.id, fan.id, kind="subscription", amount_cents=500, ref="p1")
        _add_payment(db, creator.id, fan.id, kind="subscription", amount_cents=500, ref="p2")
        _add_payment(db, creator.id, fan.id, kind="unlock", amount_cents=700, ref="p3")
        _add_payment(db, creator.id, fan.id, kind="unlock", amount_cents=300, ref="p4", status="refunded")
        expected_total = _db_completed_sum(db, creator.id)

    body = client.get("/creator/subscribers", headers=_bearer(token)).json()
    summary = body["summary"]
    assert summary["monthly_revenue_cents"] == 1000
    assert summary["one_time_revenue_cents"] == 700
    assert summary["total_revenue_cents"] == 1700
    assert summary["total_revenue_cents"] == expected_total


def test_subscriber_counts_in_summary(client, db_session):
    token = _make_creator(client)
    with db_session as db:
        creator = db.get(User, _user_id(db, "cr@example.com"))
        fans = [_make_user(db, f"cfan{i}@example.com") for i in range(5)]
        _subscribe(db, fans[0].id, creator.id, status=SubscriptionStatus.active, ref="c1")
        _subscribe(db, fans[1].id, creator.id, status=SubscriptionStatus.active, ref="c2")
        _subscribe(db, fans[2].id, creator.id, status=SubscriptionStatus.canceled, ref="c3")
        _subscribe(db, fans[3].id, creator.id, status=SubscriptionStatus.past_due, ref="c4")
        _subscribe(db, fans[4].id, creator.id, status=SubscriptionStatus.trialing, ref="c5")

    summary = client.get("/creator/subscribers", headers=_bearer(token)).json()["summary"]
    assert summary["active_subscribers"] == 2
    assert summary["canceled_subscribers"] == 1
    assert summary["past_due_subscribers"] == 1
    assert summary["trialing_subscribers"] == 1
    assert summary["total_subscribers"] == 5


def test_unlock_flow_records_and_refund_excludes_one_time_revenue(client, db_session):
    """End-to-end: an unlock charge adds to one-time revenue; its refund removes it."""
    token = _make_creator(client)
    resp = client.post(
        "/posts",
        headers=_bearer(token),
        data={"caption": "Paid", "price_cents": "500"},
        files=[("files", ("photo.jpg", _real_jpeg(), "image/jpeg"))],
    )
    assert resp.status_code == 201
    post = resp.json()
    post_id = post["id"]

    # A follower unlocks -> one completed unlock payment.
    _register(client, "fan@example.com")
    fan_token = _login(client, "fan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "fan@example.com"))
        creator = db.get(User, _user_id(db, "cr@example.com"))
        _subscribe(db, fan.id, creator.id, ref="sub_unlock_1")
        fan_id = fan.id
        creator_id = creator.id  # read before the session closes

    unlock = client.post(f"/content/{post_id}/unlock", headers=_bearer(fan_token))
    assert unlock.status_code == 201
    charge_ref = unlock.json()["unlock"]["external_ref"]

    summary = client.get("/creator/subscribers", headers=_bearer(token)).json()["summary"]
    assert summary["one_time_revenue_cents"] == 500
    assert summary["total_revenue_cents"] == 500
    with db_session as db:
        assert _db_completed_sum(db, creator_id) == 500

    # The gateway refunds the charge -> the payment row is refunded -> revenue drops.
    body, headers = _refund_webhook(
        charge_ref,
        {"subscriber_id": str(fan_id), "post_id": str(post_id)},
        "evt_refund_subs_1",
    )
    assert client.post("/webhooks/mock", data=body, headers=headers).status_code == 200

    summary = client.get("/creator/subscribers", headers=_bearer(token)).json()["summary"]
    assert summary["one_time_revenue_cents"] == 0
    assert summary["total_revenue_cents"] == 0
    with db_session as db:
        assert _db_completed_sum(db, creator.id) == 0


def test_subscription_payment_webhook_records_monthly_revenue(client, db_session):
    """A completed monthly payment (payment.succeeded) records one tier payment;
    a duplicate redelivery never records twice."""
    token = _make_creator(client)
    with db_session as db:
        creator = db.get(User, _user_id(db, "cr@example.com"))
        fan = _make_user(db, "fan@example.com")
        sub = _subscribe(db, fan.id, creator.id, status=SubscriptionStatus.incomplete, ref="sub_monthly_1")

    body = MockPaymentProvider.make_webhook_body(
        "payment.succeeded",
        external_ref=sub.external_ref,
        event_id="evt_pay_1",
    )
    headers = MockPaymentProvider.sign_body(body)
    headers["Content-Type"] = "application/json"

    first = client.post("/webhooks/mock", data=body, headers=headers)
    assert first.status_code == 200
    assert first.json()["duplicate"] is False
    summary = client.get("/creator/subscribers", headers=_bearer(token)).json()["summary"]
    assert summary["monthly_revenue_cents"] == TIER_PRICE
    assert summary["total_revenue_cents"] == TIER_PRICE

    # A provider redelivery is deduped — no second payment row.
    second = client.post("/webhooks/mock", data=body, headers=headers)
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    summary = client.get("/creator/subscribers", headers=_bearer(token)).json()["summary"]
    assert summary["monthly_revenue_cents"] == TIER_PRICE


def test_mock_activation_records_first_month(client, db_session):
    """The mock dev provider's activation event counts as the first month."""
    token = _make_creator(client)
    with db_session as db:
        creator = db.get(User, _user_id(db, "cr@example.com"))
        fan = _make_user(db, "fan@example.com")
        sub = _subscribe(db, fan.id, creator.id, status=SubscriptionStatus.incomplete, ref="sub_activate_1")

    body = MockPaymentProvider.make_webhook_body(
        "subscription.created",
        external_ref=sub.external_ref,
        subscription_status="active",
        event_id="evt_activate_1",
    )
    headers = MockPaymentProvider.sign_body(body)
    headers["Content-Type"] = "application/json"
    assert client.post("/webhooks/mock", data=body, headers=headers).status_code == 200

    summary = client.get("/creator/subscribers", headers=_bearer(token)).json()["summary"]
    assert summary["monthly_revenue_cents"] == TIER_PRICE
    with db_session as db:
        assert db.get(Subscription, sub.id).status == SubscriptionStatus.active


def test_revenue_survives_post_deletion(client, db_session):
    """Deleting a post keeps its unlock revenue (post_id is not a FK)."""
    token = _make_creator(client)
    resp = client.post(
        "/posts",
        headers=_bearer(token),
        data={"caption": "Paid", "price_cents": "700"},
        files=[("files", ("photo.jpg", _real_jpeg(), "image/jpeg"))],
    )
    post = resp.json()
    post_id = post["id"]

    _register(client, "fan@example.com")
    fan_token = _login(client, "fan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "fan@example.com"))
        creator = db.get(User, _user_id(db, "cr@example.com"))
        _subscribe(db, fan.id, creator.id, ref="sub_survive_1")
    assert (
        client.post(f"/content/{post_id}/unlock", headers=_bearer(fan_token)).status_code
        == 201
    )

    # Delete the post (and its PaidUnlock rows) — the payment row must remain.
    assert client.delete(f"/creator/content/{post_id}", headers=_bearer(token)).status_code == 204
    with db_session as db:
        assert db.scalar(select(Post).where(Post.id == post_id)) is None
        assert (
            db.scalar(
                select(Payment).where(
                    Payment.post_id == post_id,
                    Payment.status == "completed",
                )
            )
            is not None
        )

    summary = client.get("/creator/subscribers", headers=_bearer(token)).json()["summary"]
    assert summary["one_time_revenue_cents"] == 700
