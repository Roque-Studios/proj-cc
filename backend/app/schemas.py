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
    # Public hero banner on the landing page (``/media/banner/...`` after an
    # upload; None = the frontend shows a default gradient).
    banner_url: str | None
    social_links: dict[str, str] | None
    payout_info: dict | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreatorProfileUpdate(BaseModel):
    """Partial profile update — only provided fields are applied.

    ``social_links`` is a dict of platform -> handle/url (``twitter``,
    ``instagram``, ``tiktok``, ``other``); unknown platforms are rejected.
    ``None`` clears the whole block; an empty string removes one link.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    bio: str | None = Field(default=None, max_length=2000)
    avatar_url: str | None = Field(default=None, max_length=500)
    # Direct URL override for the hero banner (the upload endpoint is the usual
    # path; a url allows e.g. pasting a hosted image).
    banner_url: str | None = Field(default=None, max_length=500)
    social_links: dict[str, str] | None = None
    payout_info: dict | None = None

    @field_validator("social_links")
    @classmethod
    def _validate_social_links(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return value
        allowed = {"twitter", "instagram", "tiktok", "other"}
        cleaned = {}
        for platform, link in value.items():
            platform = platform.strip().lower()
            if platform not in allowed:
                raise ValueError(f"Unknown social platform: {platform}")
            link = (link or "").strip()
            if link:
                cleaned[platform] = link[:500]
        return cleaned


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


class SocialLinkOut(BaseModel):
    """One social account on a creator's public landing page."""

    platform: str  # twitter | instagram | tiktok | other
    label: str  # human label for the link chip
    value: str  # handle or url as stored


class CreatorLandingOut(BaseModel):
    """The public creator landing page payload.

    ``profile`` carries the public creator identity (display name, bio,
    avatar) plus the social accounts; ``viewer`` is the requesting viewer's
    access level for this creator (anonymous / registered / follower) with
    account context when authenticated; ``gateways`` are the creator's enabled
    payment gateways so the subscribe CTA can list them.
    """

    profile: CreatorLandingProfileOut
    social_links: list[SocialLinkOut]
    viewer: ViewerLandingOut
    gateways: list[CheckoutGatewayOut]


class CreatorLandingProfileOut(BaseModel):
    id: int
    username: str | None
    display_name: str | None
    bio: str | None
    avatar_url: str | None
    # Public hero banner (``/media/banner/...`` or None for the default).
    banner_url: str | None = None
    # Number of visible posts the creator has published (the hero's post count).
    post_count: int = 0


class ViewerLandingOut(BaseModel):
    """The requesting viewer's state on this creator's landing page.

    ``level`` is one of ``anonymous`` / ``registered`` / ``follower``. For
    authenticated viewers ``user_id`` and ``username`` provide the account
    context the registered view shows; ``subscription`` is the current
    subscription status when the viewer is a follower.
    """

    level: str
    user_id: int | None
    username: str | None
    subscription: str | None


class SubscribeResponse(BaseModel):
    """Result of starting a subscription: the pending row + hosted checkout url."""

    subscription: SubscriptionOut
    checkout_url: str | None
    status: str


class SubscribeStatusOut(BaseModel):
    """The viewer's subscription state for a creator — the checkout reconciler.

    Lets the checkout UI reconcile the final state after the hosted payment:
    ``subscription`` is the viewer's row for this creator (or ``None``); its
    ``status`` + ``checkout_url`` tell the UI whether payment is still
    pending (``incomplete``), succeeded (``active``/``trialing``) or failed
    (stayed incomplete / went ``past_due``/``canceled``/``expired``).
    ``viewer_level`` mirrors the access resolver (anonymous/registered/follower).
    ``tier_price_cents`` is the monthly tier price the checkout form displays.
    """

    viewer_level: str
    subscription: SubscriptionOut | None
    tier_price_cents: int


class PostMediaOut(BaseModel):
    id: int
    media_type: str
    # Null when the media is withheld (teaser for non-followers, locked paid
    # broadcast preview): the url is withheld so the feed never leaks media
    # links to viewers who can't access them.
    media_url: str | None
    # Blurred public ``PREVIEW`` teaser url (``/preview/{post_id}/media``),
    # set exactly when ``media_url`` is withheld — so non-followers see the
    # shape of the content without ever receiving the real bytes.
    preview_url: str | None = None
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
    include_preview_urls: bool = False,
) -> PostOut:
    """Build the public post shape for a specific viewer.

    ``include_media_urls=False`` (non-follower teaser / locked broadcast
    preview) withholds every media url while keeping the media *metadata*
    (count, types); ``unlocked`` reflects the viewer's access to a paid
    broadcast (``None`` when the post isn't one). When ``include_preview_urls``
    is set (the same teaser/locked cases), each withheld media carries a
    ``preview_url`` — the blurred public preview — so the frontend can render
    the post's shape without ever receiving real content bytes.
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
                preview_url=(
                    None
                    if include_media_urls or not include_preview_urls
                    else f"/preview/{post.id}/media?media_id={media.id}"
                ),
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


class CreatorPostOut(BaseModel):
    """One of the creator's own posts as shown on the content dashboard.

    Unlike the viewer-facing ``PostOut`` this always includes media urls (the
    owner can fetch their own media) plus the engagement stats: ``view_count``
    (media views served to non-owners) and ``unlock_count`` (active one-time
    unlocks — refunded unlocks are excluded).
    """

    id: int
    caption: str | None
    broadcast_price_cents: int | None
    is_visible: bool
    created_at: datetime
    updated_at: datetime
    media_count: int
    view_count: int
    unlock_count: int
    media: list[PostMediaOut]


class PostUpdate(BaseModel):
    """Partial dashboard update of one of the creator's own posts.

    ``caption`` updates the post's caption (``null`` or whitespace-only clears
    it); ``is_visible`` toggles the post's visibility to followers (a hidden
    post is soft-archived: excluded from the feed, non-owner media requests
    ``404``). Only the provided fields are applied.
    """

    caption: str | None = Field(default=None, max_length=2000)
    is_visible: bool | None = None


class SubscriberOut(BaseModel):
    """One subscription in the creator's subscriber list.

    ``started_at`` is when the subscription row was created (the start date);
    the subscriber's email/username come from the owning ``User`` row.
    """

    subscription_id: int
    subscriber_id: int
    subscriber_email: str
    subscriber_username: str | None
    status: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    started_at: datetime
    payment_provider: str | None


class RevenueSummaryOut(BaseModel):
    """The creator's revenue ledger summary.

    ``monthly_revenue_cents`` / ``one_time_revenue_cents`` are the sums of
    **completed** subscription / unlock payments (refunded rows excluded) —
    exactly the sum of completed payments in the ``payment`` ledger.
    """

    monthly_revenue_cents: int
    one_time_revenue_cents: int
    total_revenue_cents: int
    active_subscribers: int
    trialing_subscribers: int
    past_due_subscribers: int
    canceled_subscribers: int
    total_subscribers: int


class SubscriberListOut(BaseModel):
    """Paginated subscriber list + the creator's global revenue summary."""

    items: list[SubscriberOut]
    page: int
    page_size: int
    total: int
    has_more: bool
    summary: RevenueSummaryOut


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


class MessagesPageOut(BaseModel):
    """A paginated page of a conversation's message history (oldest first).

    ``before_id`` echoes the page cursor; pass it back to fetch the messages
    **older** than this page. ``has_more`` is true when older messages exist.
    """

    messages: list[MessageOut]
    before_id: int | None
    has_more: bool


class MessagesStatusOut(BaseModel):
    """Whether the current user may message a recipient (the chat input gate).

    The chat UI renders its input box only when ``can_message`` is true;
    otherwise it shows the ``reason`` explanation (e.g. a creator whose
    messaging policy is off and the sender has no existing thread). The other
    fields give the UI context: who the recipient is (creator?), whether the
    sender is an active follower, and whether a thread already exists.
    """

    recipient_id: int
    recipient_username: str | None
    recipient_is_creator: bool
    is_follower: bool
    has_conversation: bool
    messaging_enabled: bool
    can_message: bool
    reason: str



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
