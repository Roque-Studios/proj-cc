"""Shared FastAPI dependencies: current-user resolution and role checks."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import User, UserRole
from .security import decode_token
from .token_store import is_token_revoked

_bearer = HTTPBearer(auto_error=False)


def resolve_authenticated_user(
    credentials: HTTPAuthorizationCredentials | None,
    db: Session,
) -> User | None:
    """Resolve the user from a Bearer access token without raising.

    Returns None for missing/invalid/expired/revoked tokens, or inactive users.
    Used by ``get_current_user`` and the viewer access resolver.
    """
    if credentials is None:
        return None
    claims = decode_token(credentials.credentials)
    if claims is None or claims.get("type") != "access":
        return None
    if is_token_revoked(claims.get("jti", "")):
        return None
    try:
        user_id = int(claims["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the authenticated user from a Bearer access token, else 401."""
    user = resolve_authenticated_user(credentials, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return user


def require_creator(user: User = Depends(get_current_user)) -> User:
    """Reject non-creator (registered) users with 403."""
    if user.role != UserRole.creator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creator access required",
        )
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Admin access gate.

    On this single-operator platform the **creator role is the admin role**
    (product decision): the platform owner is a creator, and admin tooling
    (e.g. watermark traceability) is gated behind it. No separate admin role
    exists.
    """
    if user.role != UserRole.creator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user
