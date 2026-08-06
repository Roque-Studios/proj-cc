"""Authentication endpoints: register, login, refresh, logout, me."""

from __future__ import annotations

import re
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import User, UserRole
from ..schemas import (
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
    UserLogin,
    UserOut,
    UserRegister,
)
from ..security import (
    _DUMMY_HASH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from ..token_store import consume_token, revoke_token

router = APIRouter(prefix="/auth", tags=["auth"])

_bearer = HTTPBearer(auto_error=False)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _derive_username(email: str) -> str:
    """Derive a username from the email local part when none was provided."""
    local = re.sub(r"[^a-z0-9_]", "_", email.split("@")[0])
    return local or "user"


def _unique_username(db: Session, base: str) -> str:
    """Guarantee username uniqueness by appending a numeric suffix if needed."""
    candidate = base
    suffix = 1
    while db.scalar(select(User).where(User.username == candidate)):
        suffix += 1
        candidate = f"{base}{suffix}"
    return candidate


def _user_from_claims(db: Session, claims: dict[str, Any]) -> User | None:
    try:
        user_id = int(claims["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    return db.get(User, user_id)


def _issue_tokens(user: User) -> TokenResponse:
    subject = str(user.id)
    return TokenResponse(
        access_token=create_access_token(subject),
        refresh_token=create_refresh_token(subject),
    )


def _remaining_ttl_seconds(claims: dict[str, Any]) -> int:
    """Seconds until the token's exp, floored at 1."""
    exp = claims.get("exp")
    if not exp:
        return settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60
    return max(int(exp) - int(time.time()), 1)


def _revoke_claims(claims: dict[str, Any]) -> None:
    jti = claims.get("jti")
    if jti:
        revoke_token(jti, _remaining_ttl_seconds(claims))


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, db: Session = Depends(get_db)):
    """Create a new user. Duplicate emails are rejected with 409."""
    email = _normalize_email(str(payload.email))
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    username = _unique_username(db, payload.username or _derive_username(email))
    user = User(
        email=email,
        username=username,
        hashed_password=hash_password(payload.password),
        role=UserRole.registered,
        is_creator=False,
        is_active=True,  # immediate activation (email activation deferred)
        onboarding_complete=False,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Rare race: another request created the same email between our check and commit.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    """Authenticate and return JWT access + refresh tokens."""
    email = _normalize_email(str(payload.email))
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        # Equalize response timing so unknown emails aren't detectable.
        verify_password(payload.password, _DUMMY_HASH)
    elif not verify_password(payload.password, user.hashed_password):
        user = None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is not active",
        )
    return _issue_tokens(user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    """Rotate a valid refresh token: revoke it and issue a fresh pair."""
    claims = decode_token(payload.refresh_token)
    if claims is None or claims.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )
    user = _user_from_claims(db, claims)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )
    # Rotation: atomically consume the presented token — only its first use
    # succeeds, so a reused or concurrently replayed refresh token gets 401.
    if not consume_token(claims.get("jti", ""), _remaining_ttl_seconds(claims)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has already been used",
        )
    return _issue_tokens(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    payload: LogoutRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
):
    """Revoke the presented refresh token (and access token if provided).

    Idempotent: always succeeds with 204 so clients can log out even with
    already-expired tokens.
    """
    refresh_claims = decode_token(payload.refresh_token)
    if refresh_claims is not None and refresh_claims.get("type") == "refresh":
        _revoke_claims(refresh_claims)

    if credentials is not None:
        access_claims = decode_token(credentials.credentials)
        if access_claims is not None and access_claims.get("type") == "access":
            _revoke_claims(access_claims)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    """Return the current user for a valid access token."""
    return user
