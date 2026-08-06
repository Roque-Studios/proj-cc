"""Pydantic request/response schemas for authentication and creators."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

_PASSWORD_LOWER = re.compile(r"[a-z]")
_PASSWORD_UPPER = re.compile(r"[A-Z]")
_PASSWORD_DIGIT = re.compile(r"\d")


class UserRegister(BaseModel):
    """Registration payload: email/password (username optional, derived from email)."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    username: str | None = Field(default=None, min_length=3, max_length=50)

    @field_validator("password")
    @classmethod
    def _validate_password_complexity(cls, value: str) -> str:
        if not _PASSWORD_LOWER.search(value):
            raise ValueError("Password must contain at least one lowercase letter")
        if not _PASSWORD_UPPER.search(value):
            raise ValueError("Password must contain at least one uppercase letter")
        if not _PASSWORD_DIGIT.search(value):
            raise ValueError("Password must contain at least one digit")
        return value

    @field_validator("username")
    @classmethod
    def _strip_username(cls, value: str | None) -> str | None:
        """Reject whitespace-only usernames and store a trimmed value."""
        if value is None:
            return value
        stripped = value.strip()
        if len(stripped) < 3:
            raise ValueError("Username must be at least 3 characters")
        return stripped


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=10)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    email: EmailStr
    username: str | None
    role: str
    is_creator: bool
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class CreatorProfileOut(BaseModel):
    user_id: int
    display_name: str | None
    bio: str | None
    avatar_url: str | None
    payout_info: dict | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreatorProfileUpdate(BaseModel):
    """Partial profile update — only provided fields are applied."""

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    bio: str | None = Field(default=None, max_length=2000)
    avatar_url: str | None = Field(default=None, max_length=500)
    payout_info: dict | None = None


class SubscribeRequest(BaseModel):
    """Start a subscription to a creator at the single defined monthly tier."""

    creator_id: int
    success_url: str | None = None
    cancel_url: str | None = None


class CancelRequest(BaseModel):
    """Cancel (non-renew) an existing subscription."""

    subscription_id: int


class SubscriptionOut(BaseModel):
    id: int
    subscriber_id: int
    creator_id: int
    status: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    payment_provider: str | None
    external_ref: str | None
    checkout_url: str | None
    cancel_at_period_end: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SubscribeResponse(BaseModel):
    """Result of starting a subscription: the pending row + hosted checkout url."""

    subscription: SubscriptionOut
    checkout_url: str | None
    status: str
