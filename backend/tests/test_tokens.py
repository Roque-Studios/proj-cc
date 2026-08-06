"""Unit tests for JWT expiry, refresh-token rotation, and logout revocation.

Covers: expired access/refresh tokens -> 401, rotation (an old refresh token is
revoked once used), and logout invalidating refresh + access tokens, plus the
revocation store itself (single-use consume, TTL expiry).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jose import jwt

from app import token_store
from app.config import settings

REGISTER_PAYLOAD = {"email": "tokens@example.com", "password": "TokensPass1"}


def _register(client, **overrides):
    return client.post("/auth/register", json={**REGISTER_PAYLOAD, **overrides})


def _login(client):
    return client.post(
        "/auth/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
    )


def _craft_expired_token(subject: str = "1", token_type: str = "access") -> str:
    """Build a JWT (same secret/algorithm as the app) that already expired."""
    payload = {
        "sub": subject,
        "type": token_type,
        "jti": "expired-test-jti",
        "exp": datetime.now(timezone.utc) - timedelta(seconds=10),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


# --------------------------------------------------------------------------- #
# Token expiry
# --------------------------------------------------------------------------- #

def test_expired_access_token_returns_401(client):
    _register(client)
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {_craft_expired_token()}"})
    assert resp.status_code == 401


def test_expired_refresh_token_rejected(client):
    _register(client)
    resp = client.post("/auth/refresh", json={"refresh_token": _craft_expired_token(token_type="refresh")})
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Refresh rotation
# --------------------------------------------------------------------------- #

def test_refresh_rotates_old_token(client):
    _register(client)
    refresh_token = _login(client).json()["refresh_token"]
    resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    pair = resp.json()
    assert pair["refresh_token"] != refresh_token
    # The old refresh token was rotated -> reuse is rejected.
    assert client.post("/auth/refresh", json={"refresh_token": refresh_token}).status_code == 401


def test_refresh_issues_usable_new_refresh_token(client):
    _register(client)
    first = _login(client).json()
    second = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()
    # The new refresh token can be used for another rotation.
    assert client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 200


def test_refresh_rejects_garbage_token(client):
    resp = client.post("/auth/refresh", json={"refresh_token": "garbage-token-value"})
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Logout / revocation
# --------------------------------------------------------------------------- #

def test_logout_invalidates_refresh_token(client):
    _register(client)
    refresh_token = _login(client).json()["refresh_token"]
    resp = client.post("/auth/logout", json={"refresh_token": refresh_token})
    assert resp.status_code == 204
    assert client.post("/auth/refresh", json={"refresh_token": refresh_token}).status_code == 401


def test_logout_invalidates_access_token(client):
    _register(client)
    tokens = _login(client).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = client.post(
        "/auth/logout",
        headers=headers,
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert resp.status_code == 204
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_logout_is_idempotent_for_unknown_tokens(client):
    resp = client.post("/auth/logout", json={"refresh_token": "not-a-real-token"})
    assert resp.status_code == 204


# --------------------------------------------------------------------------- #
# Revocation store internals
# --------------------------------------------------------------------------- #

def test_consume_token_is_single_use():
    assert token_store.consume_token("single-use-jti", ttl_seconds=100) is True
    assert token_store.consume_token("single-use-jti", ttl_seconds=100) is False


def test_revoked_entry_expires_after_ttl():
    token_store.revoke_token("expiring-jti", ttl_seconds=0)  # expires immediately
    assert token_store.is_token_revoked("expiring-jti") is False


def test_revoked_access_token_rejected_on_protected_endpoint(client):
    _register(client)
    token = _login(client).json()["access_token"]
    # Logout with the access token in the header revokes it.
    resp = client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
        json={"refresh_token": "does-not-matter-here"},
    )
    assert resp.status_code == 204
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
