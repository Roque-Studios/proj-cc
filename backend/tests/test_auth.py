"""Unit tests for the authentication endpoints.

Covers: registration (happy path + duplicate email + password complexity),
login (valid JWT + wrong credentials -> 401), refresh, and the protected /me
endpoint.
"""

from __future__ import annotations

from app.models import User

REGISTER_PAYLOAD = {"email": "new@example.com", "password": "StrongPass1"}


def _register(client, **overrides):
    return client.post("/auth/register", json={**REGISTER_PAYLOAD, **overrides})


def _login(client, email="new@example.com", password="StrongPass1"):
    return client.post("/auth/login", json={"email": email, "password": password})


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

def test_register_creates_user(client):
    resp = _register(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "new@example.com"
    assert body["username"] == "new"
    assert body["role"] == "registered"
    assert body["is_creator"] is False
    assert body["is_active"] is True  # immediate activation
    assert "password" not in body
    assert "hashed_password" not in body


def test_register_normalizes_email(client):
    resp = _register(client, email="MiXeD@Example.COM")
    assert resp.status_code == 201
    assert resp.json()["email"] == "mixed@example.com"


def test_register_rejects_duplicate_email(client):
    assert _register(client).status_code == 201
    resp = _register(client, email="new@example.com", username="somename")
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


def test_register_rejects_duplicate_email_case_insensitive(client):
    assert _register(client, email="New@Example.COM").status_code == 201
    resp = _register(client, email="new@example.com")
    assert resp.status_code == 409


def test_register_rejects_whitespace_only_username(client):
    resp = _register(client, username="   ")
    assert resp.status_code == 422


def test_register_autosuffixes_colliding_username(client):
    assert _register(client).status_code == 201
    # Same local part -> derived username "new" collides -> "new2"
    resp = _register(client, email="new@example.net")
    assert resp.status_code == 201
    assert resp.json()["username"] == "new2"


def test_register_respects_explicit_username(client):
    resp = _register(client, username="myhandle")
    assert resp.status_code == 201
    assert resp.json()["username"] == "myhandle"


def test_register_rejects_short_password(client):
    resp = _register(client, password="Short1")
    assert resp.status_code == 422


def test_register_rejects_password_without_digit(client):
    resp = _register(client, password="OnlyLetters")
    assert resp.status_code == 422
    assert "digit" in str(resp.json()).lower()


def test_register_rejects_password_without_uppercase(client):
    resp = _register(client, password="lowercase1")
    assert resp.status_code == 422
    assert "uppercase" in str(resp.json()).lower()


def test_register_rejects_password_without_lowercase(client):
    resp = _register(client, password="UPPERCASE1")
    assert resp.status_code == 422
    assert "lowercase" in str(resp.json()).lower()


def test_register_rejects_invalid_email(client):
    resp = _register(client, email="not-an-email")
    assert resp.status_code == 422


def test_password_stored_as_argon2_hash(client, db_session):
    _register(client)
    with db_session as db:
        user = db.query(User).filter(User.email == "new@example.com").first()
    assert user is not None
    assert user.hashed_password.startswith("$argon2")
    assert user.hashed_password != REGISTER_PAYLOAD["password"]


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #

def test_login_returns_valid_jwt(client):
    _register(client)
    resp = _login(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]


def test_login_wrong_password_returns_401(client):
    _register(client)
    resp = _login(client, password="WrongPass1")
    assert resp.status_code == 401


def test_login_unknown_email_returns_401(client):
    resp = _login(client, email="nobody@example.com")
    assert resp.status_code == 401


def test_login_rejects_invalid_email_format(client):
    resp = client.post("/auth/login", json={"email": "nope", "password": "StrongPass1"})
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Protected endpoint (/me) — proves access tokens are usable
# --------------------------------------------------------------------------- #

def test_me_with_valid_access_token(client):
    _register(client)
    token = _login(client).json()["access_token"]
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "new@example.com"


def test_me_without_token_returns_401(client):
    assert client.get("/auth/me").status_code == 401


def test_me_with_garbage_token_returns_401(client):
    resp = client.get("/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Refresh
# --------------------------------------------------------------------------- #

def test_refresh_issues_new_tokens(client):
    _register(client)
    refresh_token = _login(client).json()["refresh_token"]
    resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


def test_refresh_rejects_garbage_token(client):
    resp = client.post("/auth/refresh", json={"refresh_token": "garbage-token-value"})
    assert resp.status_code == 401


def test_refresh_rejects_access_token_used_as_refresh(client):
    _register(client)
    access_token = _login(client).json()["access_token"]
    resp = client.post("/auth/refresh", json={"refresh_token": access_token})
    assert resp.status_code == 401
