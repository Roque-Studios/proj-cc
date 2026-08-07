"""Public creator landing endpoint tests.

Acceptance: the landing page shows role-based content per viewer state —
anonymous visitors get the subscribe prompt only (no account context),
registered non-followers get the same plus their account context and the
enabled gateways, and followers get full access (the frontend then loads the
full feed via the existing posts endpoint). Social accounts
(twitter/instagram/tiktok/other) come from the creator's profile and are
public to every level.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Subscription, SubscriptionStatus, User


def _register(client, email: str, password: str = "FeedCr123"):
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201


def _login(client, email: str, password: str = "FeedCr123"):
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_creator(client, email: str = "landcr@example.com"):
    _register(client, email)
    headers = _login(client, email)
    assert client.post("/creator/apply", headers=headers).status_code == 200
    return headers


def _creator_id(client, creator_headers) -> int:
    resp = client.get("/creator/profile", headers=creator_headers)
    assert resp.status_code == 200
    return resp.json()["user_id"]


def _user_id(db, email: str) -> int:
    return db.scalar(select(User.id).where(User.email == email))


def _follow(db, subscriber: User, creator: User) -> Subscription:
    sub = Subscription(
        subscriber_id=subscriber.id,
        creator_id=creator.id,
        status=SubscriptionStatus.active,
        current_period_start=datetime.now(timezone.utc) - timedelta(days=1),
        current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
        payment_provider="mock",
        external_ref=f"sub_land_{subscriber.id}_{creator.id}",
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


# --------------------------------------------------------------------------- #
# Role-based landing payload
# --------------------------------------------------------------------------- #


def test_landing_anonymous_sees_profile_and_prompt_only(client, db_session):
    """Anonymous: public profile + socials, anonymous level, no account context."""
    creator_headers = _make_creator(client)
    _set_social_links(
        client,
        creator_headers,
        {"twitter": "@flow", "instagram": "@flow.ig", "other": "https://flow.example"},
    )
    creator_id = _creator_id(client, creator_headers)

    resp = client.get(f"/creators/{creator_id}/landing")
    assert resp.status_code == 200
    body = resp.json()
    assert body["viewer"]["level"] == "anonymous"
    assert body["viewer"]["user_id"] is None
    assert body["viewer"]["username"] is None
    assert body["viewer"]["subscription"] is None
    assert body["profile"]["id"] == creator_id
    assert body["profile"]["username"] == "landcr"
    platforms = {s["platform"]: s["value"] for s in body["social_links"]}
    assert platforms == {
        "twitter": "@flow",
        "instagram": "@flow.ig",
        "other": "https://flow.example",
    }


def test_landing_registered_non_follower_has_account_context(client, db_session):
    """Registered non-follower: same public data + account context."""
    creator_headers = _make_creator(client)
    creator_id = _creator_id(client, creator_headers)
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")

    resp = client.get(f"/creators/{creator_id}/landing", headers=fan_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["viewer"]["level"] == "registered"
    assert body["viewer"]["user_id"] == _user_id(db_session, "fan@example.com")
    assert body["viewer"]["username"] == "fan"
    assert body["viewer"]["subscription"] is None
    assert body["profile"]["id"] == creator_id


def test_landing_follower_reports_follower_level(client, db_session):
    """Follower: the landing reports follower level + subscription status."""
    creator_headers = _make_creator(client)
    creator_id = _creator_id(client, creator_headers)
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "fan@example.com"))
        creator = db.get(User, creator_id)
        fan_id = fan.id
        _follow(db, fan, creator)

    resp = client.get(f"/creators/{creator_id}/landing", headers=fan_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["viewer"]["level"] == "follower"
    assert body["viewer"]["subscription"] == "active"
    assert body["viewer"]["user_id"] == fan_id


def test_landing_expired_subscription_is_registered(client, db_session):
    """A lapsed follower reverts to the registered view on the landing page."""
    creator_headers = _make_creator(client)
    creator_id = _creator_id(client, creator_headers)
    _register(client, "exfan@example.com")
    ex_headers = _login(client, "exfan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "exfan@example.com"))
        creator = db.get(User, creator_id)
        sub = _follow(db, fan, creator)
        sub.current_period_end = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()

    resp = client.get(f"/creators/{creator_id}/landing", headers=ex_headers)
    assert resp.status_code == 200
    assert resp.json()["viewer"]["level"] == "registered"


def test_landing_banner_and_post_count(client):
    """Landing payload carries the hero banner url + the visible post count."""
    from io import BytesIO

    from PIL import Image

    creator_headers = _make_creator(client)
    creator_id = _creator_id(client, creator_headers)

    buf = BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="JPEG")
    jpeg = buf.getvalue()
    for _ in range(2):
        resp = client.post(
            "/posts",
            headers=creator_headers,
            files={"files": ("p.jpg", jpeg, "image/jpeg")},
        )
        assert resp.status_code == 201
    # A hidden (soft-archived) post must not count.
    resp = client.post(
        "/posts", headers=creator_headers, files={"files": ("h.jpg", jpeg, "image/jpeg")}
    )
    hidden_id = resp.json()["id"]
    assert (
        client.patch(
            f"/creator/content/{hidden_id}",
            headers=creator_headers,
            json={"is_visible": False},
        ).status_code
        == 200
    )
    # Upload a banner.
    resp = client.post(
        "/creator/banner",
        headers=creator_headers,
        files={"file": ("b.jpg", jpeg, "image/jpeg")},
    )
    assert resp.status_code == 200

    body = client.get(f"/creators/{creator_id}/landing").json()
    assert body["profile"]["banner_url"] == f"/media/banner/banner_{creator_id}.jpg"
    assert body["profile"]["post_count"] == 2


def test_landing_404_for_unknown_and_non_creator(client, db_session):
    assert client.get("/creators/999999/landing").status_code == 404

    _register(client, "plain@example.com")
    plain_headers = _login(client, "plain@example.com")
    with db_session as db:
        plain_id = _user_id(db, "plain@example.com")
    # A registered (non-creator) user is not a landing page.
    assert client.get(f"/creators/{plain_id}/landing").status_code == 404


# --------------------------------------------------------------------------- #
# Default (seed) creator landing — the site-root fallback
# --------------------------------------------------------------------------- #


def test_default_landing_404_when_no_creator_exists(client, db_session):
    """No creator accounts yet -> the site root has nothing to show (404)."""
    assert client.get("/creators/default/landing").status_code == 404


def test_default_landing_returns_first_creator(client, db_session):
    """The default landing is the first (seed) creator, shaped per viewer."""
    first_headers = _make_creator(client, email="seedcr@example.com")
    first_id = _creator_id(client, first_headers)
    # A second creator must not shadow the first/seed creator.
    _make_creator(client, email="secondcr@example.com")

    resp = client.get("/creators/default/landing")
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"]["id"] == first_id
    assert body["viewer"]["level"] == "anonymous"
    assert body["viewer"]["user_id"] is None

    # Registered viewers keep their account context on the default landing.
    _register(client, "fan@example.com")
    fan_headers = _login(client, "fan@example.com")
    body = client.get("/creators/default/landing", headers=fan_headers).json()
    assert body["viewer"]["level"] == "registered"
    assert body["viewer"]["user_id"] == _user_id(db_session, "fan@example.com")
    assert body["viewer"]["username"] == "fan"


# --------------------------------------------------------------------------- #
# Social links editing + validation
# --------------------------------------------------------------------------- #


def _set_social_links(client, creator_headers, links: dict):
    resp = client.put(
        "/creator/profile", headers=creator_headers, json={"social_links": links}
    )
    assert resp.status_code == 200
    return resp.json()


def test_social_links_roundtrip_and_unknown_platform_rejected(client, db_session):
    creator_headers = _make_creator(client)
    out = _set_social_links(
        client, creator_headers, {"twitter": "@flow", "tiktok": "https://tiktok.com/@flow"}
    )
    assert out["social_links"] == {
        "twitter": "@flow",
        "tiktok": "https://tiktok.com/@flow",
    }

    # Unknown platforms are rejected (field validation -> 422).
    resp = client.put(
        "/creator/profile",
        headers=creator_headers,
        json={"social_links": {"myspace": "@flow"}},
    )
    assert resp.status_code == 422

    # Empty values remove a link (cleanup).
    out = _set_social_links(client, creator_headers, {"twitter": "  "})
    assert out["social_links"] == {}


def test_landing_socials_only_show_configured_accounts(client, db_session):
    """Unconfigured platforms never appear; ordering follows the profile dict."""
    creator_headers = _make_creator(client)
    creator_id = _creator_id(client, creator_headers)
    _set_social_links(client, creator_headers, {"instagram": "@flow.ig"})

    body = client.get(f"/creators/{creator_id}/landing").json()
    assert [s["platform"] for s in body["social_links"]] == ["instagram"]
    assert body["social_links"][0]["label"] == "Instagram"
