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
    """Start a subscription to a creator at the monthly tier.

    ``provider`` optionally picks which of the creator's enabled gateways to
    pay with (stripe/paypal/wompi/mock); when omitted, a single enabled +
    configured gateway is used (multiple or none -> 400).
    """

    creator_id: int
    provider: str | None = Field(default=None, max_length=50)
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


class PostMediaOut(BaseModel):
    id: int
    media_type: str
    # Null when the media is withheld (teaser for non-followers, locked paid
    # broadcast preview): the url is withheld so the feed never leaks media
    # links to viewers who can't access them.
    media_url: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PostOut(BaseModel):
    id: int
    creator_id: int
    caption: str | None
    # Non-null => this post is a paid broadcast; the price is the one-time
    # unlock amount in cents.
    broadcast_price_cents: int | None = None
    # The requesting viewer's unlock state: None for regular posts, True once
    # the viewer paid (or owns the post), False while the preview is locked.
    unlocked: bool | None = None
    media: list[PostMediaOut]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


def build_post_out(
    post,
    *,
    unlocked: bool | None,
    include_media_urls: bool,
) -> PostOut:
    """Build the public post shape for a specific viewer.

    ``include_media_urls=False`` (non-follower teaser / locked broadcast
    preview) withholds every media url while keeping the media *metadata*
    (count, types); ``unlocked`` reflects the viewer's access to a paid
    broadcast (``None`` when the post isn't one).
    """
    return PostOut(
        id=post.id,
        creator_id=post.creator_id,
        caption=post.caption,
        broadcast_price_cents=post.broadcast_price_cents,
        unlocked=unlocked,
        media=[
            PostMediaOut(
                id=media.id,
                media_type=media.media_type,
                media_url=media.media_url if include_media_urls else None,
                created_at=media.created_at,
            )
            for media in post.media
        ],
        created_at=post.created_at,
        updated_at=post.updated_at,
    )


class PaidUnlockOut(BaseModel):
    """A subscriber's one-time paid unlock of a paid broadcast.

    ``refunded_at`` is non-null once the gateway refunded the charge (access
    revoked until the subscriber re-purchases).
    """

    id: int
    subscriber_id: int
    post_id: int
    payment_provider: str | None
    external_ref: str | None
    refunded_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UnlockResponse(BaseModel):
    """Result of unlocking a paid broadcast.

    ``already_unlocked`` is True on an idempotent repeat of a previous unlock
    (no second charge); the ``unlock`` row is the same either way.
    """

    post_id: int
    broadcast_price_cents: int
    already_unlocked: bool
    unlock: PaidUnlockOut


class FeedResponse(BaseModel):
    """Paginated feed of a creator's posts.

    ``teaser=True`` (non-follower) withholds media urls; ``teaser=False``
    (active follower) returns the full posts.
    """

    teaser: bool
    posts: list[PostOut]
    page: int
    page_size: int
    total: int
    has_more: bool


class MessagingSettingsUpdate(BaseModel):
    """Toggle the creator's DM policy: who may start a conversation."""

    allow_messages_from_all_followers: bool


class MessagingSettingsOut(BaseModel):
    """The creator's current DM policy."""

    allow_messages_from_all_followers: bool


class MessageSend(BaseModel):
    """Send a DM to another user (creator <-> subscriber)."""

    recipient_id: int
    body: str = Field(min_length=1, max_length=2000)

    @field_validator("body")
    @classmethod
    def _body_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message body must not be empty")
        return value.strip()


class MessageOut(BaseModel):
    id: int
    conversation_id: int
    sender_id: int
    recipient_id: int
    body: str
    read_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserSummaryOut(BaseModel):
    id: int
    username: str | None


class ConversationOut(BaseModel):
    """A 1:1 DM thread from one participant's perspective.

    ``other`` is the other party in the thread (computed per requester);
    ``last_message`` is the most recent message for inbox previews.
    """

    id: int
    creator_id: int
    subscriber_id: int
    created_at: datetime
    updated_at: datetime
    other: UserSummaryOut
    last_message: MessageOut | None


class GatewayFieldOut(BaseModel):
    """One credential field of a creator's gateway settings form.

    ``configured`` reports whether the creator has a stored value for this
    field; secret values themselves are **never** returned (only the boolean),
    so the settings UI can render forms without ever echoing keys back.
    """

    name: str
    label: str
    required: bool
    secret: bool
    placeholder: str
    options: list[str]
    configured: bool


class GatewaySettingsOut(BaseModel):
    """A creator's per-gateway settings (no secret values)."""

    gateway: str
    label: str
    description: str
    enabled: bool
    # True when every required field is configured (the gateway could be enabled).
    configured: bool
    fields: list[GatewayFieldOut]


class GatewaySettingsUpdate(BaseModel):
    """Update one gateway's settings.

    ``enabled`` toggles the gateway for subscriber checkout (rejected when the
    required config is incomplete). ``config`` merges over the stored values —
    empty strings keep the existing stored value, so updates never wipe
    secrets the client cannot see.
    """

    enabled: bool | None = None
    config: dict[str, str | int] | None = None


class CheckoutGatewayOut(BaseModel):
    """One gateway a subscriber can pay with (creator enabled + configured)."""

    gateway: str
    label: str


class WatermarkTraceOut(BaseModel):
    """Result of decoding a watermark text line (admin abuse-investigation tool).

    ``user_id`` is the viewer the watermark was rendered for; ``post_id`` the
    post it was rendered from (``None`` for legacy watermarks or when the post
    hash matches no known post). ``fetched_at`` is the capture timestamp the
    watermark carried (naive UTC). ``user_matches``/``post_matches`` report how
    many ids matched the truncated hash (1 normally; more = hash collision).
    """

    viewer_hash: str
    post_hash: str | None
    fetched_at: datetime | None
    user_id: int | None
    user_email: str | None
    post_id: int | None
    post_caption: str | None
    user_matches: int
    post_matches: int
