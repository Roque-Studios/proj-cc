"""Creator-to-subscriber DM service (1:1 threads).

A conversation is the unique ``(creator_id, subscriber_id)`` pair — the thread
grouping — and every message between the same two people lands in it. Sending
is gated by the creator's ``allow_messages_from_all_followers`` policy:

- the creator may always message a subscriber (this is what creates the
  "existing thread" a subscriber can then reply into even with the policy off);
- a subscriber reaching a creator is **always** allowed to continue an
  existing conversation — the acceptance's carve-out ("blocked ... if the
  sender isn't already in an existing thread"); a subscriber with **no**
  existing thread must be an active follower, and when the creator's policy is
  off, starting a new thread is blocked with a clear error.

``MessageGateError`` maps to 403 (policy/follower blocks) and ``ValueError``
to 400 (malformed requests) in the router.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..access import is_active_follower
from ..models import (
    Conversation,
    CreatorProfile,
    Message,
    MessageMedia,
    User,
    UserRole,
)


class MessageGateError(Exception):
    """A message was blocked by the messaging policy (maps to 403)."""


class UnknownRecipientError(Exception):
    """The recipient does not exist (maps to 404)."""


class MessageService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #

    def send(
        self,
        sender: User,
        recipient_id: int,
        body: str,
        *,
        price_cents: int | None = None,
        media: list[tuple[str, str]] = (),  # (storage_key, media_type)
    ) -> Message:
        """Deliver a message, applying the DM policy gate.

        ``price_cents`` marks the message **paid** (one-time unlock); ``media``
        is a list of ``(storage_key, media_type)`` uploads already persisted
        to storage (the router saves them; a failed commit leaves the caller
        to clean up). Returns the persisted ``Message`` (in its conversation —
        created on first contact, reused on every subsequent message).
        """
        body = body.strip()
        if not body and not media:
            raise ValueError("Message body must not be empty")

        recipient = self.db.get(User, recipient_id)
        if recipient is None or not recipient.is_active:
            raise UnknownRecipientError("Recipient not found")
        if recipient.id == sender.id:
            raise ValueError("You cannot message yourself")
        # Only a creator may price a message — subscribers can't sell content.
        # Enforced here (the service) so every send path (JSON, multipart and
        # any future caller) is covered, not just one endpoint.
        if price_cents is not None and sender.role != UserRole.creator:
            raise ValueError("Only creators can send paid content")

        if sender.role == UserRole.creator:
            # The creator may always reach a subscriber — this is what creates
            # the thread a subscriber can later continue. DMs are strictly
            # creator <-> subscriber, so two creators can't be in a thread
            # together (the pair is directional and would split into two).
            if recipient.role == UserRole.creator:
                raise ValueError(
                    "DMs are between a creator and their subscribers"
                )
            creator, subscriber = sender, recipient
        else:
            # A subscriber reaching a creator: continuing an existing thread is
            # always allowed (acceptance carve-out); starting a new one needs
            # an active subscription + the creator's policy.
            if recipient.role != UserRole.creator:
                raise ValueError(
                    "You can only send messages to creators"
                )
            creator, subscriber = recipient, sender
            if self._conversation_between(creator.id, subscriber.id) is None:
                if not is_active_follower(self.db, sender.id, creator.id):
                    raise MessageGateError(
                        "Only followers can message this creator"
                    )
                profile = self.db.scalar(
                    select(CreatorProfile).where(
                        CreatorProfile.user_id == creator.id
                    )
                )
                if profile is not None and not profile.allow_messages_from_all_followers:
                    raise MessageGateError(
                        "This creator has messaging turned off — you can only "
                        "message them if you already have an existing conversation"
                    )

        conversation = self._conversation_between(creator.id, subscriber.id)
        if conversation is None:
            conversation = Conversation(
                creator_id=creator.id,
                subscriber_id=subscriber.id,
            )
            self.db.add(conversation)
            try:
                self.db.flush()
            except IntegrityError:
                # A concurrent send created the thread first — reuse it.
                self.db.rollback()
                conversation = self._conversation_between(creator.id, subscriber.id)
                if conversation is None:  # pragma: no cover - defensive
                    raise

        message = Message(
            conversation_id=conversation.id,
            sender_id=sender.id,
            recipient_id=recipient.id,
            body=body,
            price_cents=price_cents,
        )
        self.db.add(message)
        self.db.flush()  # assign message.id for the media rows
        for storage_key, media_type in media:
            self.db.add(
                MessageMedia(
                    message_id=message.id,
                    media_type=media_type,
                    storage_key=storage_key,
                )
            )
        # Touch the thread so the inbox orders by recency.
        conversation.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(message)
        return message

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def conversation_between(
        self, creator_id: int, subscriber_id: int
    ) -> Conversation | None:
        """The thread for a (creator, subscriber) pair, or None."""
        return self._conversation_between(creator_id, subscriber_id)

    def last_message(self, conversation_id: int) -> Message | None:
        """The most recent message in a thread (inbox preview), or None."""
        return self.db.scalar(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(1)
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _conversation_between(
        self, creator_id: int, subscriber_id: int
    ) -> Conversation | None:
        return self.db.scalar(
            select(Conversation).where(
                Conversation.creator_id == creator_id,
                Conversation.subscriber_id == subscriber_id,
            )
        )
