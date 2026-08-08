"""Client-side proof-of-work for the auth endpoints (bot cost-raising).

The server issues a signed challenge; the client must find a ``nonce`` such
that ``sha256(challenge + \".\" + nonce)`` has at least ``difficulty`` leading
zero bits (WebCrypto on the frontend — a few hundred milliseconds at 16
bits). The challenge is **stateless-signed** with ``SECRET_KEY`` (HMAC-SHA256
of ``challenge + issued_at``), so issuing needs no storage; verification
recomputes the signature and enforces the TTL.

Replay protection: on first successful verification the challenge is claimed
through the rate-limit store's ``consume_once`` primitive (single-use for the
TTL) — the same challenge cannot be replayed for a second request. If the
store is unavailable the claim fails open (the rate limiter still throttles).

``AUTH_POW_DIFFICULTY = 0`` disables the check entirely (dev / tests / proxy
environments) — the endpoint just accepts requests without a proof.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from .config import settings
from .ratelimit import consume_once

_SEP = "."


def _sign(challenge: str, issued_at: int) -> str:
    msg = f"{challenge}{_SEP}{issued_at}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def issue_challenge() -> dict:
    """A fresh challenge for the client to solve.

    The client echoes ``challenge`` / ``issued_at`` / ``signature`` back with
    its ``nonce``; verification recomputes the signature from the key.
    """
    challenge = secrets.token_hex(16)
    issued_at = int(time.time())
    return {
        "challenge": challenge,
        "issued_at": issued_at,
        "signature": _sign(challenge, issued_at),
        "difficulty": settings.AUTH_POW_DIFFICULTY,
        "ttl_seconds": settings.AUTH_POW_TTL_SECONDS,
    }


def _leading_zero_bits(digest_hex: str) -> int:
    """Count leading zero bits of a hex-encoded 256-bit digest."""
    value = int(digest_hex, 16)
    return 256 - value.bit_length()


def verify(
    challenge: str,
    issued_at: int,
    signature: str,
    nonce: str,
) -> bool:
    """True when the proof is fresh, correctly signed and meets the difficulty.

    Consumes the challenge (single-use) on success — a replay of the same
    challenge returns False even with a valid signature + nonce.
    """
    if settings.AUTH_POW_DIFFICULTY <= 0:
        return True  # PoW disabled — nothing to verify
    if not challenge or not nonce:
        return False
    if not hmac.compare_digest(signature, _sign(challenge, issued_at)):
        return False
    age = int(time.time()) - issued_at
    if age < 0 or age > settings.AUTH_POW_TTL_SECONDS:
        return False
    digest = hashlib.sha256(f"{challenge}{_SEP}{nonce}".encode()).hexdigest()
    if _leading_zero_bits(digest) < settings.AUTH_POW_DIFFICULTY:
        return False
    # Claim the challenge so it can't be replayed within its TTL.
    return consume_once(f"pow:{challenge}", settings.AUTH_POW_TTL_SECONDS)
