"""Pydantic request/response schemas for authentication and creators."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

_PASSWORD_LOWER = re.compile(r"[a-z]")
_PASSWORD_UPPER = re.compile(r"[A-Z]")
_PASSWORD_DIGIT = re.compile(r"\d")


def validate_password_complexity(value: str) -> str:
    """Enforce the shared password rule (register + change-password)."""
    if not _PASSWORD_LOWER.search(value):
        raise ValueError("Password must contain at least one lowercase letter")
    if not _PASSWORD_UPPER.search(value):
        raise ValueError("Password must contain at least one uppercase letter")
    if not _PASSWORD_DIGIT.search(value):
        raise ValueError("Password must contain at least one digit")
    return value


class UserRegister(BaseModel):
    """Registration payload: email/password (username optional, derived from email)."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    username: str | None = Field(default=None, min_length=3, max_length=50)

    @field_validator("password")
    @classmethod
    def _validate_password_complexity(cls, value: str) -> str:
        return validate_password_complexity(value)

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


class ChangePasswordRequest(BaseModel):
    """Change the authenticated user's password.

    ``current_password`` must match the stored hash; ``new_password`` follows
    the same complexity rules as registration (the profile page enforces them
    client-side too).
    """

    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class ForgotPasswordRequest(BaseModel):
    """Request a password reset code for an email address."""

    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    """Result of a reset-code request.

    The response never reveals whether the account exists (``sent`` is always
    True). ``dev_token`` is only populated when no SMTP mail server is
    configured — the development affordance that hands the reset code to the
    requester directly instead of emailing it.
    """

    sent: bool = True
    dev_token: str | None = None


class ResetPasswordRequest(BaseModel):
    """Set a new password with a reset code.

    ``token`` is the short-lived reset JWT (``type: "reset"``) from
    ``POST /auth/forgot-password``; ``new_password`` follows the same
    complexity rules as registration.
    """

    token: str = Field(min_length=10, max_length=2048)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _validate_password_complexity(cls, value: str) -> str:
        return validate_password_complexity(value)

    @field_validator("new_password")
    @classmethod
    def _validate_password_complexity(cls, value: str) -> str:
        return validate_password_complexity(value)


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
    # Whether the creator has a live (unexpired) 24-hour story — turns the
    # avatar indicator green on the landing/feed pages. Public: the story
    # *content* stays follower-only, the badge is just the signal.
    has_active_story: bool = False


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


class MySubscriptionOut(BaseModel):
    """One subscription in the authenticated user's profile.

    ``creator_id``/``creator_username``/``creator_display_name`` identify the
    creator they subscribed to; ``days_left`` is the whole days remaining in
    the current billing period (``None`` when the row has no period end or is
    no longer active).
    """

    subscription_id: int
    creator_id: int
    creator_username: str | None
    creator_display_name: str | None
    status: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    payment_provider: str | None
    created_at: datetime
    days_left: int | None


class MySubscriptionsOut(BaseModel):
    """The authenticated user's subscriptions (newest first)."""

    items: list[MySubscriptionOut]


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
    # Engagement counters shown on every post (teaser viewers included — the
    # numbers are public, the actions are gated). ``liked_by_me`` is only ever
    # True for the authenticated viewer who liked it.
    like_count: int = 0
    comment_count: int = 0
    liked_by_me: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


def build_post_out(
    post,
    *,
    unlocked: bool | None,
    include_media_urls: bool,
    include_preview_urls: bool = False,
    like_count: int = 0,
    comment_count: int = 0,
    liked_by_me: bool = False,
) -> PostOut:
    """Build the public post shape for a specific viewer.

    ``include_media_urls=False`` (non-follower teaser / locked broadcast
    preview) withholds every media url while keeping the media *metadata*
    (count, types); ``unlocked`` reflects the viewer's access to a paid
    broadcast (``None`` when the post isn't one). When ``include_preview_urls``
    is set (the same teaser/locked cases), each withheld media carries a
    ``preview_url`` — the blurred public preview — so the frontend can render
    the post's shape without ever receiving real content bytes.

    ``like_count`` / ``comment_count`` are the post's engagement totals the
    caller computed (bulk-counted per feed page); ``liked_by_me`` is whether
    the requesting viewer already liked this post.
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
        like_count=like_count,
        comment_count=comment_count,
        liked_by_me=liked_by_me,
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
    (no second charge); ``checkout_url`` is the hosted payment page the
    subscriber is sent to pay on (None when already unlocked).
    """

    post_id: int
    broadcast_price_cents: int
    already_unlocked: bool
    checkout_url: str | None = None
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


class MediaGalleryItemOut(BaseModel):
    """One media file in the flat MEDIA gallery of a creator's content.

    The gallery flattens every post's media into one stream (newest post
    first) so the UI can render a grid of the creator's full content. Gating
    mirrors the feed exactly: ``media_url`` is set for followers (and the
    owner) on free posts and unlocked broadcasts; **locked paid broadcasts**
    withhold it and instead carry ``preview_url`` (the blurred preview) plus
    the one-time ``broadcast_price_cents`` and ``unlocked: False``; everyone
    else (anonymous/registered) gets ``preview_url`` on everything
    (``teaser=True`` on the page). ``post_caption``/``created_at`` give each
    tile post context for captions and ordering.
    """

    media_id: int
    post_id: int
    media_type: str
    # The real watermarked url (auth-gated) when accessible — withheld
    # (None) for locked paid broadcasts and for every non-follower item.
    media_url: str | None
    # The blurred public preview — set exactly when ``media_url`` is withheld.
    preview_url: str | None = None
    broadcast_price_cents: int | None = None
    # None for free posts; True once unlocked (or the owner); False while
    # the one-time payment is still owed.
    unlocked: bool | None = None
    post_caption: str | None = None
    created_at: datetime


class MediaGalleryResponse(BaseModel):
    """Paginated flat media gallery of a creator's content.

    ``teaser=True`` (non-follower) withholds every media url (all tiles are
    blurred previews); ``teaser=False`` (active follower/owner) returns real
    urls except for locked paid broadcasts, which stay blurred with price.
    """

    teaser: bool
    items: list[MediaGalleryItemOut]
    page: int
    page_size: int
    total: int
    has_more: bool


class CreatorPostOut(BaseModel):
    """One of the creator's own posts as shown on the content dashboard.

    Unlike the viewer-facing ``PostOut`` this always includes media urls (the
    owner can fetch their own media) plus the engagement stats: ``view_count``
    (media views served to non-owners), ``unlock_count`` (active one-time
    unlocks — refunded unlocks are excluded) and the like/comment totals
    (``like_count`` / ``comment_count``).
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
    like_count: int = 0
    comment_count: int = 0
    media: list[PostMediaOut]


class PostLikeResponse(BaseModel):
    """Result of a like/unlike toggle on a post.

    ``liked`` is the new state (True after like, False after unlike);
    ``like_count`` is the post's total so the client can update its counter
    without a refetch.
    """

    post_id: int
    liked: bool
    like_count: int


class CommentCreate(BaseModel):
    """Create a comment on a post (text + emojis only — no media)."""

    body: str = Field(min_length=1, max_length=500)

    @field_validator("body")
    @classmethod
    def _body_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Comment must not be empty")
        return stripped


class CommentOut(BaseModel):
    """One comment on a post, with the author's display context.

    ``author_username`` is the account handle; ``author_display_name`` is the
    creator's display name (creators only — subscribers render their
    username); ``author_avatar_url`` is the creator's public avatar url
    (subscribers have none — the UI shows an initials circle).
    """

    id: int
    post_id: int
    user_id: int
    body: str
    author_username: str | None
    author_display_name: str | None = None
    author_avatar_url: str | None = None
    author_is_creator: bool = False
    created_at: datetime


class CommentsPageOut(BaseModel):
    """A paginated page of a post's comments (newest first)."""

    items: list[CommentOut]
    page: int
    page_size: int
    total: int
    has_more: bool


class PostUpdate(BaseModel):
    """Partial dashboard update of one of the creator's own posts.

    ``caption`` updates the post's caption (``null`` or whitespace-only clears
    it); ``is_visible`` toggles the post's visibility to followers (a hidden
    post is soft-archived: excluded from the feed, non-owner media requests
    ``404``). Only the provided fields are applied.
    """

    caption: str | None = Field(default=None, max_length=2000)
    is_visible: bool | None = None


class StoryMediaOut(BaseModel):
    """One media file of a 24-hour story (always owner/follower-shaped).

    Story endpoints are auth-gated (creator or active follower), so
    ``media_url`` is never withheld — unlike post media there is no teaser
    tier for stories.
    """

    id: int
    media_type: str
    media_url: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StoryOut(BaseModel):
    """A creator's 24-hour story with its media.

    ``expires_at`` tells the client when the story disappears; expired stories
    are never returned by any endpoint (the creator dashboard is the only
    place expired stories can still be seen, via ``GET /creator/stories``).
    """

    id: int
    creator_id: int
    caption: str | None
    expires_at: datetime
    created_at: datetime
    media: list[StoryMediaOut]

    model_config = ConfigDict(from_attributes=True)


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
    # One-time unlock price in cents — when set, the message is a **paid
    # message** whose media the recipient unlocks for a one-time payment.
    price_cents: int | None = Field(default=None, ge=1, le=100_000)

    @field_validator("body")
    @classmethod
    def _body_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message body must not be empty")
        return value.strip()


class MessageMediaOut(BaseModel):
    id: int
    message_id: int
    media_type: str
    media_url: str

    model_config = ConfigDict(from_attributes=True)


class MessageOut(BaseModel):
    id: int
    conversation_id: int
    sender_id: int
    recipient_id: int
    body: str
    price_cents: int | None = None
    media: list[MessageMediaOut] = []
    # The requesting viewer's access to a paid message: None for free
    # messages, True once unlocked (or the sender themselves), False while
    # still locked.
    unlocked: bool | None = None
    read_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


def build_message_out(message, unlocked: bool | None) -> MessageOut:
    """Shape a ``Message`` row for one viewer.

    ``unlocked`` is the caller-computed access state (None for free messages;
    True for the sender or an active unlock; False for a locked paid message).
    Media urls are always the auth-gated ``/messages/{id}/media`` endpoint.
    """
    return MessageOut(
        id=message.id,
        conversation_id=message.conversation_id,
        sender_id=message.sender_id,
        recipient_id=message.recipient_id,
        body=message.body,
        price_cents=message.price_cents,
        media=[
            MessageMediaOut(
                id=m.id,
                message_id=m.message_id,
                media_type=m.media_type,
                media_url=m.media_url,
            )
            for m in message.media
        ],
        unlocked=unlocked,
        read_at=message.read_at,
        created_at=message.created_at,
    )


class MessageUnlockResponse(BaseModel):
    """Result of unlocking a paid message.

    ``checkout_url`` is the hosted payment page the subscriber is redirected
    to (None when already unlocked — an idempotent repeat).
    """

    message_id: int
    price_cents: int
    already_unlocked: bool
    checkout_url: str | None = None


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
    # The other party's avatar url (creator profiles only — subscribers have
    # no avatar upload). ``None``/missing -> the UI shows an initials avatar.
    avatar_url: str | None = None


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
    ``value`` echoes the stored value for **non-secret** fields only (e.g. the
    environment select) so the form can pre-fill them — always ``None`` for
    secret fields.
    """

    name: str
    label: str
    required: bool
    secret: bool
    placeholder: str
    options: list[str]
    configured: bool
    value: str | None = None


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
