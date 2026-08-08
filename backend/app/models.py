import enum
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    Enum as SQLEnum,
)
from sqlalchemy.orm import relationship

from .database import Base


class UserRole(enum.Enum):
    registered = "registered"
    creator = "creator"


class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(
        SQLEnum(UserRole),
        default=UserRole.registered,
        server_default=UserRole.registered.value,
        nullable=False,
    )
    is_creator = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)
    onboarding_complete = Column(Boolean, default=False, nullable=False)
    activation_token = Column(String, nullable=True)
    # Gateway-neutral external customer id (e.g. the Stripe customer id),
    # created lazily on first payment flow and cached here.
    payment_customer_id = Column(String(255), nullable=True)

    creator_profile = relationship(
        "CreatorProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    gateway_configs = relationship(
        "CreatorGatewayConfig",
        back_populates="creator",
        cascade="all, delete-orphan",
    )
    # passive_deletes: user deletion is delegated to the DB (FK ON DELETE
    # CASCADE). Tests bulk-delete children first since SQLite doesn't enforce FKs.
    subscriptions = relationship(
        "Subscription",
        back_populates="subscriber",
        foreign_keys="Subscription.subscriber_id",
        passive_deletes=True,
    )
    creator_subscriptions = relationship(
        "Subscription",
        back_populates="creator",
        foreign_keys="Subscription.creator_id",
        passive_deletes=True,
    )


class SubscriptionStatus(enum.Enum):
    active = "active"
    trialing = "trialing"
    incomplete = "incomplete"  # payment pending — not yet a follower
    past_due = "past_due"
    canceled = "canceled"
    expired = "expired"


class Subscription(Base):
    """A subscriber's subscription to one specific creator.

    Scoped per creator: a user holds one row per creator they subscribe to, so
    each (subscriber_id, creator_id) pair is unique and a subscriber can have
    independent statuses across different creators.
    """

    __tablename__ = "subscription"
    __table_args__ = (
        UniqueConstraint(
            "subscriber_id",
            "creator_id",
            name="uq_subscription_subscriber_creator",
        ),
        # A gateway subscription id (or checkout session id, pre-adoption)
        # uniquely identifies one local row per provider. Webhook reconciliation
        # looks rows up by this ref, so ambiguity would corrupt it.
        UniqueConstraint(
            "payment_provider",
            "external_ref",
            name="uq_subscription_provider_ref",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    subscriber_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    creator_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    status = Column(
        SQLEnum(SubscriptionStatus),
        default=SubscriptionStatus.active,
        server_default=SubscriptionStatus.active.value,
        nullable=False,
    )
    current_period_start = Column(DateTime(timezone=True), nullable=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True)
    payment_provider = Column(String(50), nullable=True)
    external_ref = Column(String(255), index=True, nullable=True)
    # Hosted checkout URL for the pending (incomplete) payment, returned to the
    # client to complete. Cleared once the subscription activates.
    checkout_url = Column(String(500), nullable=True)
    # Non-renew flag: set when the subscriber cancels; the subscription stays
    # active (access persists) until current_period_end, then a scheduled task
    # flips the status to canceled.
    cancel_at_period_end = Column(Boolean, default=False, nullable=False, server_default="false")
    # Consent record captured at checkout: the subscriber confirmed they are
    # 18+ (``age_confirmed``) and accepted the creator's Terms of Service
    # (``tos_accepted_at`` stamped at creation). Written in the same
    # transaction as the pending row — the consent audit trail for the checkout
    # gate.
    age_confirmed = Column(Boolean, default=False, nullable=False, server_default="false")
    tos_accepted_at = Column(DateTime(timezone=True), nullable=True)
    # The monthly price in cents **snapshotted at checkout** (the creator's
    # ``tier_price_cents`` or the platform default). NULL on legacy rows =
    # ``settings.SUBSCRIPTION_TIER_PRICE_CENTS`` at read time. The webhook
    # reconciler records this exact amount in the revenue ledger, so renewals
    # stay priced at what the subscriber agreed to pay.
    tier_price_cents = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    subscriber = relationship(
        "User",
        back_populates="subscriptions",
        foreign_keys=[subscriber_id],
    )
    creator = relationship(
        "User",
        back_populates="creator_subscriptions",
        foreign_keys=[creator_id],
    )


class ProcessedWebhookEvent(Base):
    """Idempotency ledger for verified webhook events.

    Providers redeliver events when we don't answer 2xx or on transient
    failures. Recording each processed ``(provider, event_id)`` pair lets the
    webhook handler recognize retries and skip re-applying status changes (e.g.
    no duplicate renewal / duplicate failure notifications). The marker is
    written in the same transaction as the reconciliation it deduplicates.
    """

    __tablename__ = "processed_webhook_event"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "event_id",
            name="uq_webhook_event_provider_id",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(50), nullable=False)
    event_id = Column(String(255), nullable=False)
    processed_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Post(Base):
    """A creator's post (photo-post for now), visible to followers only.

    Access gating happens at the feed/read layer (see the viewer access
    resolver); the model itself just scopes posts to their creator.

    A post with ``broadcast_price_cents`` set is a **paid broadcast**: it goes
    to all subscribers as a locked preview, and each subscriber needs a one-time
    payment (``PaidUnlock``) for full media access.
    """

    __tablename__ = "post"

    id = Column(Integer, primary_key=True, index=True)
    creator_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    caption = Column(Text, nullable=True)
    # One-time unlock price in cents; NULL = a regular (free) post.
    broadcast_price_cents = Column(Integer, nullable=True)
    # Whether the post is shown to followers (feed + media). A hidden post is
    # soft-archived: only its creator can still view/edit it (dashboard); the
    # public feed excludes it and non-owner media/unlock requests 404.
    is_visible = Column(Boolean, default=True, nullable=False, server_default="true")
    # Engagement: total media views served to non-owner authorized viewers
    # (each GET of a media file counts; owner views and HEAD requests don't).
    view_count = Column(Integer, default=0, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    media = relationship(
        "PostMedia",
        back_populates="post",
        cascade="all, delete-orphan",
        order_by="PostMedia.id",
    )
    unlocks = relationship(
        "PaidUnlock",
        back_populates="post",
        cascade="all, delete-orphan",
        # FK has ON DELETE CASCADE: post deletion is delegated to the DB.
        passive_deletes=True,
    )
    likes = relationship(
        "PostLike",
        back_populates="post",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    comments = relationship(
        "PostComment",
        back_populates="post",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PostMedia(Base):
    """A media file attached to a post (validated image upload).

    ``storage_key`` is an unguessable uuid + extension and names the private
    original in the storage layer; it is **never exposed in a public URL**.
    Clients reference media through the auth-gated, watermarked
    ``/content/{post_id}/media?media_id={id}`` endpoint instead (see
    ``app.routers.content``), so the unguessable key can't be used to fetch
    content without a valid subscription.
    """

    __tablename__ = "post_media"

    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(
        Integer,
        ForeignKey("post.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    media_type = Column(String(50), nullable=False)  # e.g. image/jpeg
    storage_key = Column(String(255), unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    post = relationship("Post", back_populates="media")

    @property
    def media_url(self) -> str:
        """Public URL for this media file (auth + watermark applied on fetch).

        ``<img>`` tags can pass the access token via ``?token=``. The storage
        key itself is never part of any URL.
        """
        return f"/content/{self.post_id}/media?media_id={self.id}"


class PostLike(Base):
    """A subscriber's like on a post.

    One row per (post, user): liking twice is idempotent (the unique pair is
    enforced), so the client toggles by inserting/removing this row. Likes
    follow the post's content gate — only the creator and their active
    followers can like (the feed reports the count to everyone, but the action
    itself is gated like the rest of the content).
    """

    __tablename__ = "post_like"
    __table_args__ = (
        UniqueConstraint(
            "post_id",
            "user_id",
            name="uq_post_like_post_user",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(
        Integer,
        ForeignKey("post.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    post = relationship("Post", back_populates="likes")


class PostComment(Base):
    """A subscriber's comment on a post (text + emojis only — no media).

    ``body`` is validated at the API layer (1..500 chars, not blank). Comments
    follow the same content gate as likes: creator + active followers can read
    and write them; the author (or the post's creator) may delete one.
    """

    __tablename__ = "post_comment"

    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(
        Integer,
        ForeignKey("post.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    post = relationship("Post", back_populates="comments")


class PaidUnlock(Base):
    """A subscriber's one-time paid unlock of a paid broadcast (post).

    One row per (subscriber, broadcast): unlocking is a one-time purchase, so
    the unique pair is enforced. The payment goes through the same **hosted
    checkout + webhook** pattern as subscriptions (``PaymentProvider.
    create_one_time_link``): the subscriber pays on the gateway's page and the
    ``payment.succeeded`` webhook activates the unlock (``paid_at`` set,
    ``checkout_url`` cleared). A row created without a payment yet is
    **pending** (``paid_at`` NULL) and simply re-surfaces its ``checkout_url``
    on repeat unlock attempts.

    ``refunded_at`` is set when the gateway refunds the charge (a verified
    ``payment.refunded`` webhook): access is revoked until the subscriber
    re-purchases, at which point the same row is reactivated in place (the
    unique pair still holds one row).
    """

    __tablename__ = "paid_unlock"
    __table_args__ = (
        UniqueConstraint(
            "subscriber_id",
            "post_id",
            name="uq_paid_unlock_subscriber_post",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    subscriber_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    post_id = Column(
        Integer,
        ForeignKey("post.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    payment_provider = Column(String(50), nullable=True)
    external_ref = Column(String(255), nullable=True)
    # The hosted payment link the subscriber pays on (NULL once paid — the
    # webhook clears it; also NULL for pre-hosted-flow rows).
    checkout_url = Column(String(500), nullable=True)
    # When the payment webhook activated the unlock (NULL = pending payment).
    paid_at = Column(DateTime(timezone=True), nullable=True)
    # Set when the gateway refunded this charge — access is revoked until the
    # subscriber re-purchases (NULL while the unlock is in force).
    refunded_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    post = relationship("Post", back_populates="unlocks")


class Payment(Base):
    """One completed (or refunded) payment in a creator's revenue ledger.

    A row is written **atomically with the transaction that recorded the
    charge**: a monthly subscription payment (each successful payment event /
    activation) or a one-time broadcast unlock. The revenue dashboard sums the
    *completed* rows; a refunded unlock marks its payment row ``refunded`` so
    it drops out of revenue. ``post_id`` deliberately has **no FK** — revenue
    history must survive a post being deleted (the money was collected).
    """

    __tablename__ = "payment"

    id = Column(Integer, primary_key=True, index=True)
    creator_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    subscriber_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # "subscription" (monthly tier) | "unlock" (one-time broadcast unlock).
    kind = Column(String(20), nullable=False)
    amount_cents = Column(Integer, nullable=False)
    # "completed" | "refunded" (a refunded unlock's charge is excluded from revenue).
    status = Column(String(20), default="completed", nullable=False, server_default="completed")
    payment_provider = Column(String(50), nullable=True)
    external_ref = Column(String(255), index=True, nullable=True)
    # For unlocks; intentionally NOT a FK so revenue survives post deletion.
    post_id = Column(Integer, nullable=True)
    # For paid-message unlocks (same no-FK rationale as ``post_id``).
    message_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CreatorGatewayConfig(Base):
    """A creator's per-gateway payment configuration.

    One row per (creator, gateway): which gateways the creator accepts for
    subscriber checkout and the credentials for that gateway. Credentials are
    **strictly per-creator** — there is no fallback to platform env keys — so a
    gateway only appears in a subscriber's checkout once the creator enabled it
    with a complete config (see ``app.gateways`` for the required fields per
    gateway and the enable validation).

    ``config`` holds the gateway's credential fields as a JSON dict (e.g.
    ``{"secret_key": "sk_live_...", "webhook_secret": "whsec_..."}``). Secret
    values are never returned by API responses — read paths surface per-field
    ``configured`` booleans only.
    """

    __tablename__ = "creator_gateway_config"
    __table_args__ = (
        UniqueConstraint(
            "creator_id",
            "gateway",
            name="uq_creator_gateway_config_creator_gateway",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    creator_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    gateway = Column(String(50), nullable=False)
    enabled = Column(Boolean, default=False, nullable=False, server_default="false")
    config = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    creator = relationship("User", back_populates="gateway_configs")


class Conversation(Base):
    """A 1:1 DM thread between one creator and one subscriber.

    The unique ``(creator_id, subscriber_id)`` pair is the **thread grouping**:
    every message between the same two people lands in the same conversation,
    and starting a new thread for an existing pair is impossible (the unique
    constraint makes it idempotent). A conversation is what "an existing
    thread" means for the messaging gate — a subscriber can only message a
    creator whose ``allow_messages_from_all_followers`` setting is off if a
    conversation already exists between them.
    """

    __tablename__ = "conversation"
    __table_args__ = (
        UniqueConstraint(
            "creator_id",
            "subscriber_id",
            name="uq_conversation_creator_subscriber",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    creator_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    subscriber_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Touched on every message so the inbox can order threads by recency.
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )


class Message(Base):
    """One DM within a conversation (creator <-> subscriber, 1:1).

    ``sender_id`` / ``recipient_id`` are denormalized from the conversation for
    cheap read-side rendering; the thread grouping lives on the Conversation
    row.
    """

    __tablename__ = "message"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(
        Integer,
        ForeignKey("conversation.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    sender_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    recipient_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    body = Column(Text, nullable=False)
    # One-time unlock price in cents for a **paid message** (creator sends
    # exclusive media the recipient pays once to view). NULL = a regular DM.
    price_cents = Column(Integer, nullable=True)
    # Set once the recipient has seen the message (nullable until then).
    read_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    conversation = relationship("Conversation", back_populates="messages")
    media = relationship(
        "MessageMedia",
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="MessageMedia.id",
    )


class MessageMedia(Base):
    """A media file attached to a DM message (validated image upload).

    Clients reference media through the auth-gated, watermarked
    ``/messages/{message_id}/media?media_id={id}`` endpoint (participants only;
    paid messages stay blurred/locked until the one-time unlock). The raw
    storage key is never exposed — exactly like ``PostMedia``.
    """

    __tablename__ = "message_media"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(
        Integer,
        ForeignKey("message.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    media_type = Column(String(50), nullable=False)
    storage_key = Column(String(500), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    message = relationship("Message", back_populates="media")

    @property
    def media_url(self) -> str:
        """Public url for this message media (auth + watermark on fetch)."""
        return f"/messages/{self.message_id}/media?media_id={self.id}"


class PaidMessageUnlock(Base):
    """A subscriber's one-time paid unlock of a paid DM message.

    Same lifecycle as ``PaidUnlock`` (hosted checkout + webhook activation):
    one row per (subscriber, message); ``paid_at`` marks the activated
    payment, ``checkout_url`` the hosted link before payment, ``refunded_at``
    a gateway refund (access revoked until re-purchase).
    """

    __tablename__ = "paid_message_unlock"
    __table_args__ = (
        UniqueConstraint(
            "subscriber_id",
            "message_id",
            name="uq_paid_message_unlock_subscriber_message",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    subscriber_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    message_id = Column(
        Integer,
        ForeignKey("message.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    payment_provider = Column(String(50), nullable=True)
    external_ref = Column(String(255), nullable=True)
    checkout_url = Column(String(500), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    refunded_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    message = relationship("Message")


class Story(Base):
    """A creator's 24-hour story (follower-only ephemeral content).

    A story is one or more validated images that auto-expire 24 hours after
    creation (``expires_at``): once expired it is invisible everywhere — the
    follower listing, media serving, and the green "story live" avatar
    indicator all stop reporting it. Access gating mirrors posts: only the
    story's creator and active followers can list it or fetch its media
    (see ``app.routers.stories``).
    """

    __tablename__ = "story"

    id = Column(Integer, primary_key=True, index=True)
    creator_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    caption = Column(Text, nullable=True)
    # UTC instant at which the story disappears (``created_at`` + 24h).
    expires_at = Column(DateTime(timezone=True), index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    media = relationship(
        "StoryMedia",
        back_populates="story",
        cascade="all, delete-orphan",
        order_by="StoryMedia.id",
    )


class StoryMedia(Base):
    """A media file attached to a 24-hour story (validated image upload).

    Same private-original model as ``PostMedia``: ``storage_key`` names the
    unguessable private file and is **never exposed**; clients fetch media
    through the auth-gated, watermarked ``/stories/{story_id}/media`` endpoint
    (story creator or active follower only).
    """

    __tablename__ = "story_media"

    id = Column(Integer, primary_key=True, index=True)
    story_id = Column(
        Integer,
        ForeignKey("story.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    media_type = Column(String(50), nullable=False)  # e.g. image/jpeg
    storage_key = Column(String(255), unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    story = relationship("Story", back_populates="media")

    @property
    def media_url(self) -> str:
        """Public URL for this story media (auth + watermark on fetch)."""
        return f"/stories/{self.story_id}/media?media_id={self.id}"


class BlockedUser(Base):
    """A subscriber the creator has blocked (banned).

    One row per (creator, blocked user) — the unique pair means blocking is
    idempotent and unblocking simply removes the row. A blocked user loses
    every access to that creator: they are demoted from ``follower`` in the
    access resolver (feed, media, stories, likes, comments and unlocks all
    gate on it), DMs to the creator are rejected, and they cannot subscribe
    (``POST /subscribe`` returns 403 while blocked). Blocking also cancels any
    active subscription the user holds with the creator (the row is marked
    ``canceled`` locally — no gateway call, so no charge is reversed; the
    subscriber keeps their paid period but has no access until unblocked, at
    which point they can re-subscribe).
    """

    __tablename__ = "blocked_user"
    __table_args__ = (
        UniqueConstraint(
            "creator_id",
            "user_id",
            name="uq_blocked_user_creator_user",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    creator_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CreatorProfile(Base):
    """Creator profile extension (one-to-one with User).

    Holds the creator-facing fields: display name, bio, avatar, a payout
    info placeholder that will be filled in by the payments integration, and
    the DM policy (``allow_messages_from_all_followers``).
    """

    __tablename__ = "creator_profile"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    display_name = Column(String(100), nullable=True)
    bio = Column(Text, nullable=True)
    avatar_url = Column(String(500), nullable=True)
    # Public hero banner on the creator's landing page. Set via the creator's
    # banner upload endpoint, which stores the file and stores its public URL
    # here (``/media/banner/...``); None = the frontend shows a default
    # gradient banner.
    banner_url = Column(String(500), nullable=True)
    # Public social-media account handles/urls shown on the creator's public
    # landing page: {"twitter": "@handle", "instagram": "@handle",
    # "tiktok": "@handle", "other": "https://..."}. Keys are the supported
    # platforms; values are free-form (handles or full urls) — the frontend
    # renders links from them. ``None``/empty = no link for that platform.
    social_links = Column(JSON, nullable=True)
    payout_info = Column(JSON, nullable=True)  # placeholder, e.g. {"method": "...", "email": "..."}
    # DM policy: when True, every active follower may start a conversation.
    # When False, followers can only continue an **existing** conversation
    # (threads the creator started, or started while the setting was on).
    allow_messages_from_all_followers = Column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    # The creator's Terms of Service and Privacy Policy texts shown to
    # subscribers before checkout (the admin ``Legal`` tab edits them). NULL or
    # blank = the platform defaults in ``app.legal`` are served instead, so a
    # creator can never leave subscribers without a policy.
    tos_text = Column(Text, nullable=True)
    privacy_text = Column(Text, nullable=True)
    # The creator's own monthly subscription price in cents (set from the admin
    # Settings tab). NULL/0 = the platform default
    # ``settings.SUBSCRIPTION_TIER_PRICE_CENTS`` (``$5.00``). The price is
    # snapshotted onto each subscription row at checkout so renewals and the
    # revenue ledger keep the price the subscriber actually paid.
    tier_price_cents = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user = relationship("User", back_populates="creator_profile")
