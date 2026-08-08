"""Anti-bot tests: honeypot + proof-of-work on the auth endpoints.

- **Honeypot**: a hidden ``website`` field real users never see. When it is
  filled the endpoint silently fake-succeeds — no account is created (register),
  no tokens are minted (login) and no reset code is issued (forgot-password) —
  so bots believe they won and move on.
- **Proof-of-work**: when ``AUTH_POW_DIFFICULTY > 0``, register / login /
  forgot-password require a freshly signed challenge solved with the right
  number of leading-zero bits. Missing, tampered, expired or **replayed**
  proofs are rejected with 403; the challenge is single-use.
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select

from app.models import User
from app.pow import _sign, issue_challenge


def _solve(challenge: str, difficulty: int, nonce_start: int = 0) -> str:
    """Brute-force a nonce with ``difficulty`` leading zero bits (test helper)."""
    n = nonce_start
    while True:
        digest = hashlib.sha256(f"{challenge}.{n}".encode()).hexdigest()
        if 256 - int(digest, 16).bit_length() >= difficulty:
            return str(n)
        n += 1


def _register(client, **overrides):
    payload = {"email": "bot@example.com", "password": "StrongPass1"}
    payload.update(overrides)
    return client.post("/auth/register", json=payload)


# --------------------------------------------------------------------------- #
# Honeypot
# --------------------------------------------------------------------------- #

def test_register_honeypot_fake_success_no_row(client, db_session):
    resp = _register(client, website="http://spam.example/")
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == 0  # fabricated, not a real user
    assert body["is_active"] is False
    with db_session as db:
        assert db.scalar(select(User.id).where(User.email == "bot@example.com")) is None


def test_login_honeypot_no_tokens(client):
    # Register a real user so the "would-be" login is for a valid account.
    _register(client)
    assert client.post(
        "/auth/login",
        json={"email": "bot@example.com", "password": "StrongPass1"},
    ).status_code == 200
    resp = client.post(
        "/auth/login",
        json={
            "email": "bot@example.com",
            "password": "StrongPass1",
            "website": "http://spam.example/",
        },
    )
    assert resp.status_code == 200
    # The fake token is a placeholder — never a real JWT.
    assert resp.json()["access_token"] == "honeypot-rejected"


def test_forgot_password_honeypot_fake_success(client, db_session):
    _register(client)
    resp = client.post(
        "/auth/forgot-password",
        json={"email": "bot@example.com", "website": "http://spam.example/"},
    )
    assert resp.status_code == 200
    assert resp.json()["sent"] is True
    assert resp.json().get("dev_token") is None  # no code was issued


# --------------------------------------------------------------------------- #
# Proof-of-work
# --------------------------------------------------------------------------- #

@pytest.fixture()
def pow_enabled(monkeypatch):
    """Enable PoW at a test-friendly difficulty (8 bits ≈ 256 hashes)."""
    monkeypatch.setattr("app.config.settings.AUTH_POW_DIFFICULTY", 8)
    monkeypatch.setattr("app.config.settings.AUTH_POW_TTL_SECONDS", 120)
    return 8


def _proof(difficulty: int, **overrides):
    ch = issue_challenge()
    proof = {
        "challenge": ch["challenge"],
        "issued_at": ch["issued_at"],
        "signature": ch["signature"],
        "nonce": _solve(ch["challenge"], difficulty),
    }
    proof.update(overrides)
    return proof


def test_register_requires_pow_when_enabled(client, pow_enabled):
    resp = _register(client)
    assert resp.status_code == 403
    assert "proof-of-work" in resp.json()["detail"].lower()


def test_register_accepts_valid_pow(client, pow_enabled):
    resp = _register(client, pow=_proof(pow_enabled))
    assert resp.status_code == 201, resp.text


def test_register_rejects_wrong_nonce(client, pow_enabled):
    proof = _proof(pow_enabled)
    proof["nonce"] = "0"  # almost certainly not a valid proof
    resp = _register(client, pow=proof)
    assert resp.status_code == 403


def test_register_rejects_tampered_signature(client, pow_enabled):
    proof = _proof(pow_enabled)
    proof["signature"] = "0" * 64
    resp = _register(client, pow=proof)
    assert resp.status_code == 403


def test_register_rejects_stale_challenge(client, pow_enabled, monkeypatch):
    proof = _proof(pow_enabled)
    # Rewind the issued_at past the TTL: the signature was computed over the
    # original timestamp, so a tampered one is also invalid — the expiry check
    # is belt-and-braces for a legitimately old challenge.
    proof["issued_at"] = proof["issued_at"] - 200
    # Re-sign with the old timestamp so only the age is wrong.
    proof["signature"] = _sign(proof["challenge"], proof["issued_at"])
    resp = _register(client, pow=proof)
    assert resp.status_code == 403


def test_pow_challenge_is_single_use(client, pow_enabled):
    proof = _proof(pow_enabled)
    assert _register(client, pow=proof).status_code == 201
    # The same challenge cannot be replayed (for a second register attempt).
    resp = _register(client, email="again@example.com", pow=proof)
    assert resp.status_code == 403


def test_login_requires_pow_when_enabled(client, pow_enabled):
    # With PoW enabled, register also needs a proof — create the account with one.
    assert _register(client, pow=_proof(pow_enabled)).status_code == 201
    resp = client.post(
        "/auth/login",
        json={"email": "bot@example.com", "password": "StrongPass1"},
    )
    assert resp.status_code == 403
    resp = client.post(
        "/auth/login",
        json={
            "email": "bot@example.com",
            "password": "StrongPass1",
            "pow": _proof(pow_enabled),
        },
    )
    assert resp.status_code == 200


def test_forgot_password_requires_pow(client, pow_enabled):
    resp = client.post(
        "/auth/forgot-password", json={"email": "bot@example.com"}
    )
    assert resp.status_code == 403
    resp = client.post(
        "/auth/forgot-password",
        json={"email": "bot@example.com", "pow": _proof(pow_enabled)},
    )
    assert resp.status_code == 200


def test_pow_verify_direct(pow_enabled):
    from app.pow import verify

    ch = issue_challenge()
    nonce = _solve(ch["challenge"], pow_enabled)
    assert (
        verify(
            ch["challenge"], ch["issued_at"], ch["signature"], nonce
        )
        is True
    )
    assert (
        verify(
            ch["challenge"], ch["issued_at"], ch["signature"], "0"
        )
        is False
    )
