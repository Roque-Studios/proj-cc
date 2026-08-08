"""Consent + legal-documents tests for the subscribe flow.

Covers the checkout consent gate acceptance:

- **Age verification + TOS consent are required before any payment starts**:
  ``POST /subscribe`` rejects requests that don't confirm ``age_confirmed``
  and ``accepted_tos``, and a successful request records the consent audit
  trail on the subscription row (``age_confirmed`` + ``tos_accepted_at``).
- **Legal documents are always available**: the landing payload and the
  creator profile serve the effective Terms of Service / Privacy Policy —
  the creator's own text when set, otherwise the ``app.legal`` platform
  defaults (drafted for AI-generated content).
"""

from __future__ import annotations

from sqlalchemy import select

from app.legal import DEFAULT_PRIVACY, DEFAULT_TOS
from app.models import CreatorGatewayConfig, Subscription, User


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


def _make_creator(client, db, email: str = "creator@example.com") -> int:
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
    return user.id


def _consent_payload(creator_id: int, **overrides) -> dict:
    payload = {
        "creator_id": creator_id,
        "accepted_tos": True,
        "age_confirmed": True,
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Consent gate
# --------------------------------------------------------------------------- #


def test_subscribe_without_consent_400(client, db_session):
    with db_session as db:
        creator_id = _make_creator(client, db)
    headers = _register(client, "sub@example.com")
    resp = client.post(
        "/subscribe",
        json=_consent_payload(creator_id, accepted_tos=False, age_confirmed=False),
        headers=headers,
    )
    assert resp.status_code == 400
    assert "18" in resp.json()["detail"]
    assert "Terms" in resp.json()["detail"]


def test_subscribe_without_tos_acceptance_400(client, db_session):
    with db_session as db:
        creator_id = _make_creator(client, db)
    headers = _register(client, "sub@example.com")
    resp = client.post(
        "/subscribe",
        json=_consent_payload(creator_id, accepted_tos=False),
        headers=headers,
    )
    assert resp.status_code == 400


def test_subscribe_without_age_confirmation_400(client, db_session):
    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator_id = _make_creator(client, db)
        subscriber = db.scalar(select(User).where(User.email == "sub@example.com"))
        subscriber_id = subscriber.id

    resp = client.post(
        "/subscribe",
        json=_consent_payload(creator_id, age_confirmed=False),
        headers=headers,
    )
    assert resp.status_code == 400
    # Nothing was created — no pending row exists for the pair.
    with db_session as db:
        sub = db.scalar(
            select(Subscription).where(
                Subscription.subscriber_id == subscriber_id,
                Subscription.creator_id == creator_id,
            )
        )
    assert sub is None


def test_subscribe_with_consent_records_audit_trail(client, db_session):
    headers = _register(client, "sub@example.com")
    with db_session as db:
        creator_id = _make_creator(client, db)

    resp = client.post(
        "/subscribe",
        json=_consent_payload(creator_id),
        headers=headers,
    )
    assert resp.status_code == 201

    with db_session as db:
        sub = db.scalar(select(Subscription).where(Subscription.id == resp.json()["subscription"]["id"]))
        assert sub is not None
        assert sub.age_confirmed is True
        assert sub.tos_accepted_at is not None


# --------------------------------------------------------------------------- #
# Legal documents: defaults + creator customization
# --------------------------------------------------------------------------- #


def test_landing_serves_default_legal_documents(client, db_session):
    with db_session as db:
        creator_id = _make_creator(client, db)
    body = client.get(f"/creators/{creator_id}/landing").json()
    profile = body["profile"]
    # The served text is whitespace-normalized (stripped) at the edges.
    assert (profile["tos_text"] or "").rstrip() == DEFAULT_TOS.rstrip()
    assert (profile["privacy_text"] or "").rstrip() == DEFAULT_PRIVACY.rstrip()
    # The default is drafted for AI-generated content.
    assert "AI" in profile["tos_text"]
    assert "artificial intelligence" in profile["privacy_text"].lower()


def test_creator_profile_serves_defaults_and_saves_custom(client, db_session):
    headers = _register(client, "creator@example.com")
    client.post("/creator/apply", headers=headers)

    # Defaults are served before any customization.
    profile = client.get("/creator/profile", headers=headers).json()
    assert (profile["tos_text"] or "").rstrip() == DEFAULT_TOS.rstrip()
    assert (profile["privacy_text"] or "").rstrip() == DEFAULT_PRIVACY.rstrip()

    custom_tos = "My custom Terms of Service for this creator."
    custom_privacy = "My custom Privacy Policy for this creator."
    updated = client.put(
        "/creator/profile",
        json={"tos_text": custom_tos, "privacy_text": custom_privacy},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["tos_text"] == custom_tos
    assert updated.json()["privacy_text"] == custom_privacy

    # The landing page reflects the creator's own documents.
    creator_id = updated.json()["user_id"]
    landing = client.get(f"/creators/{creator_id}/landing").json()
    assert landing["profile"]["tos_text"] == custom_tos
    assert landing["profile"]["privacy_text"] == custom_privacy


def test_blank_custom_text_falls_back_to_defaults(client, db_session):
    headers = _register(client, "creator@example.com")
    client.post("/creator/apply", headers=headers)

    # Saving blank text clears the custom value -> defaults are served again.
    client.put(
        "/creator/profile",
        json={"tos_text": "  ", "privacy_text": ""},
        headers=headers,
    )
    profile = client.get("/creator/profile", headers=headers).json()
    assert (profile["tos_text"] or "").rstrip() == DEFAULT_TOS.rstrip()
    assert (profile["privacy_text"] or "").rstrip() == DEFAULT_PRIVACY.rstrip()
