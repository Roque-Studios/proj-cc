"""Password hashing (Argon2id) and JWT access/refresh token helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from jose import JWTError, jwt

from .config import settings

logger = structlog.get_logger()

_ALGORITHM = "HS256"

_hasher = PasswordHasher()

# Hash of an unlikely password, verified against unknown emails so login
# response timing doesn't leak whether an account exists (user enumeration).
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing-equalization")


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id and return the encoded hash."""
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify a plaintext password against an encoded Argon2id hash."""
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False


def create_access_token(subject: str) -> str:
    """Issue a short-lived JWT access token for ``subject`` (user id as str)."""
    return _create_token(subject, "access", settings.ACCESS_TOKEN_EXPIRE_MINUTES)


def create_refresh_token(subject: str) -> str:
    """Issue a longer-lived JWT refresh token for ``subject``."""
    return _create_token(subject, "refresh", settings.REFRESH_TOKEN_EXPIRE_MINUTES)


def create_reset_token(subject: str) -> str:
    """Issue a short-lived, single-purpose JWT for a password reset.

    The ``type`` claim is ``"reset"`` (not ``"access"``/``"refresh"``) so a
    stolen reset code can never be used as a session token, and the reset
    endpoint rejects any other token type.
    """
    return _create_token(subject, "reset", settings.RESET_TOKEN_EXPIRE_MINUTES)


def _create_token(subject: str, token_type: str, minutes: int) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        # jti uniquely identifies the token so it can be revoked/rotated.
        "jti": uuid.uuid4().hex,
        "exp": expires_at,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a JWT. Returns the payload, or None if invalid/expired."""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[_ALGORITHM])
    except JWTError:
        return None
