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


class PaidUnlock(Base):
    """A subscriber's one-time paid unlock of a paid broadcast (post).

    One row per (subscriber, broadcast): unlocking is a one-time purchase, so
    the unique pair is enforced. The payment is a one-time charge through the
    payment abstraction (``PaymentProvider.charge_one_time``) — entirely
    separate from the monthly subscription charge — and the provider ref is
    kept for reconciliation.

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
    # Set when the gateway refunded this charge — access is revoked until the
    # subscriber re-purchases (NULL while the unlock is in force).
    refunded_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    post = relationship("Post", back_populates="unlocks")


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
    # Set once the recipient has seen the message (nullable until then).
    read_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    conversation = relationship("Conversation", back_populates="messages")


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
    payout_info = Column(JSON, nullable=True)  # placeholder, e.g. {"method": "...", "email": "..."}
    # DM policy: when True, every active follower may start a conversation.
    # When False, followers can only continue an **existing** conversation
    # (threads the creator started, or started while the setting was on).
    allow_messages_from_all_followers = Column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user = relationship("User", back_populates="creator_profile")
