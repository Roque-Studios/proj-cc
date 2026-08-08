"""Authentication endpoints: register, login, refresh, logout, me."""

from __future__ import annotations

import re
import time
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import User, UserRole
from ..pow import issue_challenge, verify as verify_pow
from ..ratelimit import (
    check_rate_limit,
    client_ip,
    email_scope_key,
    rate_limit,
)
from ..schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LogoutRequest,
    RefreshRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserLogin,
    UserOut,
    UserRegister,
)
from ..security import (
    _DUMMY_HASH,
    create_access_token,
    create_refresh_token,
    create_reset_token,
    decode_token,
    hash_password,
    verify_password,
)
from ..token_store import consume_token, revoke_token

logger = structlog.get_logger()

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


@router.get(
    "/pow-challenge",
    response_model=dict,
    # Light per-IP budget — issuing is cheap, but an unthrottled challenge
    # endpoint is a free CPU/bandwidth amplifier with no cost to the caller.
    dependencies=[rate_limit("pow-challenge", window_seconds=60, max_requests=30)],
)
def pow_challenge():
    """Issue a signed proof-of-work challenge for the auth forms.

    The client solves it (WebCrypto SHA-256 nonce search) and submits the
    proof with register / login / forgot-password. When PoW is disabled
    (``AUTH_POW_DIFFICULTY=0``) this still returns a shape with ``difficulty:
    0`` so the client can skip the work.
    """
    return issue_challenge()


def _reject_honeypot() -> bool:
    """True when the honeypot gate is enabled and should silently fake-success.

    The actual fake-success response is produced by the caller (it must match
    the endpoint's response model) — the helper just decides the gate.
    """
    return settings.AUTH_HONEYPOT_ENABLED


def _require_pow(pow_proof) -> None:
    """Reject a request whose proof-of-work is missing/invalid (when enabled)."""
    if settings.AUTH_POW_DIFFICULTY <= 0:
        return
    if pow_proof is None or not verify_pow(
        pow_proof.challenge,
        pow_proof.issued_at,
        pow_proof.signature,
        pow_proof.nonce,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Proof-of-work verification failed — please refresh and try again.",
        )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(
    payload: UserRegister,
    request: Request,
    db: Session = Depends(get_db),
    _: None = rate_limit("register", window_seconds=3600, max_requests=5),
):
    """Create a new user. Duplicate emails are rejected with 409.

    Rate-limited per IP (5/hour). Honeypot-filled requests silently fake-
    succeed (no account created) so bots believe they won. Requires a valid
    proof-of-work when PoW is enabled.
    """
    if _reject_honeypot() and (payload.website or "").strip():
        # Fake success: same 201 shape, no row, no work done.
        logger.info("register honeypot tripped", ip=client_ip(request))
        return UserOut(
            id=0,
            email=str(payload.email),
            username=payload.username or "bot",
            role="registered",
            is_creator=False,
            is_active=False,
        )
    _require_pow(payload.pow)
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
def login(
    payload: UserLogin,
    request: Request,
    db: Session = Depends(get_db),
    _: None = rate_limit("login", window_seconds=300, max_requests=20),
):
    """Authenticate and return JWT access + refresh tokens.

    Rate-limited per IP (20/5 min) and per (IP + email) (5/15 min) to slow
    credential stuffing without allowing a permanent email lockout.
    """
    email = _normalize_email(str(payload.email))
    if _reject_honeypot() and (payload.website or "").strip():
        logger.info("login honeypot tripped", ip=client_ip(request))
        return TokenResponse(
            access_token="honeypot-rejected",
            refresh_token="honeypot-rejected",
        )
    _require_pow(payload.pow)
    # Per (IP, email) budget — checked inline (the body is only parsed here).
    ip = client_ip(request)
    check_rate_limit(
        "login-ip-email",
        f"{ip}:{email_scope_key(email)}",
        window_seconds=900,
        max_requests=5,
    )
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
def refresh(
    payload: RefreshRequest,
    db: Session = Depends(get_db),
    _: None = rate_limit("refresh", window_seconds=600, max_requests=30),
):
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


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    _: None = rate_limit("forgot", window_seconds=3600, max_requests=5),
):
    """Request a password-reset code for an email.

    Never reveals whether the account exists (always ``sent: True``). When
    SMTP is configured the code is emailed (best-effort task — the request
    never waits on mail); otherwise the code is returned as ``dev_token`` so
    the flow works without a mail server (development / mock setups).
    """
    email = _normalize_email(payload.email)
    if _reject_honeypot() and (payload.website or "").strip():
        logger.info("forgot-password honeypot tripped", ip=client_ip(request))
        return ForgotPasswordResponse(sent=True)
    _require_pow(payload.pow)
    check_rate_limit(
        "forgot-email",
        email_scope_key(email),
        window_seconds=3600,
        max_requests=3,
    )
    user = db.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
    if user is None:
        # Same response as success: don't let the endpoint enumerate accounts.
        return ForgotPasswordResponse(sent=True)

    token = create_reset_token(str(user.id))
    if settings.SMTP_HOST:
        try:
            from ..tasks import notify_password_reset

            notify_password_reset.delay(user.email, token)
        except Exception as exc:  # noqa: BLE001 — mail must never break auth
            logger.exception("failed to enqueue password reset email", error=str(exc))
        return ForgotPasswordResponse(sent=True)
    logger.warning(
        "password reset code returned in dev mode (SMTP not configured)",
        user_id=user.id,
        recipient=user.email,
    )
    return ForgotPasswordResponse(sent=True, dev_token=token)


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
    _: None = rate_limit("reset", window_seconds=3600, max_requests=20),
):
    """Set a new password with a short-lived reset code.

    Only ``type: "reset"`` tokens are accepted — an access or refresh token
    can never be replayed here. Invalid, expired or wrong-typed codes are all
    answered identically (400) so the endpoint can't be used to probe tokens.
    Rate-limited per IP (20/hour) and per email (10/hour).
    """
    claims = decode_token(payload.token)
    if claims is None or claims.get("type") != "reset":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code",
        )
    user = _user_from_claims(db, claims)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code",
        )
    check_rate_limit(
        "reset-email",
        email_scope_key(user.email),
        window_seconds=3600,
        max_requests=10,
    )
    user.hashed_password = hash_password(payload.new_password)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = rate_limit("change-pw", window_seconds=3600, max_requests=10),
):
    """Verify the current password and set a new one.

    The new password follows the same complexity rules as registration
    (validated by the schema). Existing access tokens keep working; the client
    typically signs the user out after a change so the next login uses the new
    password.
    """
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    user.hashed_password = hash_password(payload.new_password)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
