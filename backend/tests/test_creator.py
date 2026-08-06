"""Unit tests for the creator role model and creator-only endpoints.

Covers: default role (registered), is_creator flag, 403 for registered users on
creator-only endpoints, the self-serve apply flow, and creator profile CRUD.
"""

from __future__ import annotations

from app.models import CreatorProfile

REGISTER_PAYLOAD = {"email": "creator@example.com", "password": "CreatorPass1"}


def _register(client, **overrides):
    return client.post("/auth/register", json={**REGISTER_PAYLOAD, **overrides})


def _login(client, email="creator@example.com", password="CreatorPass1"):
    return client.post("/auth/login", json={"email": email, "password": password})


def _auth_header(client):
    token = _login(client).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# Role model defaults
# --------------------------------------------------------------------------- #

def test_registered_user_has_registered_role(client):
    resp = _register(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["role"] == "registered"
    assert body["is_creator"] is False


def test_new_user_has_no_creator_profile(client, db_session):
    _register(client)
    with db_session as db:
        assert db.query(CreatorProfile).count() == 0


# --------------------------------------------------------------------------- #
# Creator-only access control
# --------------------------------------------------------------------------- #

def test_creator_endpoint_rejects_registered_user_with_403(client):
    _register(client)
    resp = client.get("/creator/profile", headers=_auth_header(client))
    assert resp.status_code == 403


def test_creator_update_rejects_registered_user_with_403(client):
    _register(client)
    resp = client.put(
        "/creator/profile",
        headers=_auth_header(client),
        json={"display_name": "Nope"},
    )
    assert resp.status_code == 403


def test_creator_endpoint_requires_auth(client):
    assert client.get("/creator/profile").status_code == 401


# --------------------------------------------------------------------------- #
# Apply flow
# --------------------------------------------------------------------------- #

def test_apply_upgrades_user_to_creator(client):
    _register(client)
    resp = client.post("/creator/apply", headers=_auth_header(client))
    assert resp.status_code == 200
    assert resp.json()["user_id"] is not None

    me = client.get("/auth/me", headers=_auth_header(client))
    assert me.status_code == 200
    assert me.json()["role"] == "creator"
    assert me.json()["is_creator"] is True


def test_apply_creates_profile_stub_with_display_name(client):
    _register(client)
    resp = client.post("/creator/apply", headers=_auth_header(client))
    assert resp.json()["display_name"] == "creator"  # derived username


def test_apply_is_idempotent(client):
    _register(client)
    headers = _auth_header(client)
    assert client.post("/creator/apply", headers=headers).status_code == 200
    assert client.post("/creator/apply", headers=headers).status_code == 200


# --------------------------------------------------------------------------- #
# Creator profile
# --------------------------------------------------------------------------- #

def test_creator_profile_accessible_after_apply(client):
    _register(client)
    client.post("/creator/apply", headers=_auth_header(client))
    resp = client.get("/creator/profile", headers=_auth_header(client))
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "creator"


def test_creator_profile_update(client):
    _register(client)
    client.post("/creator/apply", headers=_auth_header(client))
    resp = client.put(
        "/creator/profile",
        headers=_auth_header(client),
        json={
            "display_name": "Fernando Flow",
            "bio": "Video creator",
            "avatar_url": "https://example.com/avatar.png",
            "payout_info": {"method": "paypal", "email": "pay@example.com"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "Fernando Flow"
    assert body["bio"] == "Video creator"
    assert body["avatar_url"] == "https://example.com/avatar.png"
    assert body["payout_info"] == {"method": "paypal", "email": "pay@example.com"}


def test_creator_profile_partial_update_preserves_other_fields(client):
    _register(client)
    client.post("/creator/apply", headers=_auth_header(client))
    client.put(
        "/creator/profile",
        headers=_auth_header(client),
        json={"display_name": "Fernando Flow"},
    )
    resp = client.put(
        "/creator/profile",
        headers=_auth_header(client),
        json={"bio": "Just the bio changed"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bio"] == "Just the bio changed"
    assert body["display_name"] == "Fernando Flow"  # unchanged
