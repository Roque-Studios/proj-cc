"""DM endpoints: send a message and read conversations.

``POST /messages`` delivers a creator-to-subscriber DM (1:1). The messaging
gate lives in ``MessageService``: a subscriber must be an active follower, and
a creator whose ``allow_messages_from_all_followers`` setting is off can only
be reached by followers who already have an **existing conversation** with
them — starting a new thread is blocked with a clear error.

``GET /conversations`` lists the requester's threads (with the other party and
a last-message preview); ``GET /conversations/{id}/messages`` is the message
history — participants only (an outsider gets the same 404 as a missing
thread, so conversation ids don't leak).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import realtime
from ..access import is_active_follower
from ..database import get_db
from ..deps import get_current_user
from ..models import Conversation, CreatorProfile, Message, User, UserRole
from ..schemas import (
    ConversationOut,
    MessageOut,
    MessageSend,
    MessagesPageOut,
    MessagesStatusOut,
    UserSummaryOut,
)
from ..services.messages import MessageGateError, MessageService, UnknownRecipientError

router = APIRouter(tags=["messages"])


@router.post("/messages", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
async def send_message(
    payload: MessageSend,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send a DM. Blocked with a clear 403 when the creator's policy forbids a
    new thread from this sender.

    Persists through the same gate as the WebSocket path, then pushes the
    message live to the recipient's connected sockets (any worker) via the
    realtime manager — so REST sends reach connected recipients in real time
    too, and the REST history is the fallback for anyone offline.
    """
    try:
        message = MessageService(db).send(user, payload.recipient_id, payload.body)
    except MessageGateError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except UnknownRecipientError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    payload_out = MessageOut.model_validate(message).model_dump(mode="json")
    await realtime.manager.send_to_user(
        message.recipient_id,
        {"type": "message", "message": payload_out},
        message_id=message.id,
    )
    return message


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The requester's DM threads, most recent first, with the other party
    and a last-message preview."""
    service = MessageService(db)
    conversations = db.scalars(
        select(Conversation)
        .where(
            or_(
                Conversation.creator_id == user.id,
                Conversation.subscriber_id == user.id,
            )
        )
        .order_by(Conversation.updated_at.desc())
    ).all()
    return [_conversation_out(db, service, user, conv) for conv in conversations]


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=MessagesPageOut,
)
def conversation_messages(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    before_id: int | None = Query(default=None, ge=1),
):
    """A thread's message history (oldest first), paginated by id cursor.

    Participants only (an outsider gets the same 404 as a missing thread).
    With ``before_id`` the page contains the ``limit`` messages **older** than
    that id — the scroll-up pagination cursor; without it, the most recent
    ``limit`` messages (the initial load). The response echoes ``before_id``
    (the id to pass next, or ``None`` at the start of the thread) and
    ``has_more``.
    """
    conversation = db.get(Conversation, conversation_id)
    if (
        conversation is None
        or user.id not in (conversation.creator_id, conversation.subscriber_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    # Fetch the newest ``limit`` messages before the cursor, newest first.
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(limit)
    )
    if before_id is not None:
        stmt = stmt.where(Message.id < before_id)
    rows = db.scalars(stmt).all()
    oldest_loaded = rows[-1].id if rows else None
    # One extra row tells us whether older messages exist.
    has_more = False
    if rows:
        older = db.scalar(
            select(Message.id)
            .where(
                Message.conversation_id == conversation_id,
                Message.id < oldest_loaded,
            )
            .limit(1)
        )
        has_more = older is not None

    return MessagesPageOut(
        messages=[MessageOut.model_validate(m) for m in reversed(rows)],
        before_id=oldest_loaded,
        has_more=has_more,
    )


@router.get("/messages/status", response_model=MessagesStatusOut)
def messages_status(
    recipient_id: int = Query(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Whether the current user may message a recipient, and why not.

    Drives the chat UI's input gate: ``can_message`` false renders the
    disabled-messaging explanation instead of the composer. Mirrors the
    ``MessageService`` gate (follower + policy + existing-thread carve-out)
    without sending anything.
    """
    recipient = db.get(User, recipient_id)
    if recipient is None or not recipient.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recipient not found",
        )

    recipient_is_creator = recipient.role == UserRole.creator
    is_follower = is_active_follower(db, user.id, recipient_id)
    has_conversation = (
        MessageService(db).conversation_between(recipient_id, user.id) is not None
        or MessageService(db).conversation_between(user.id, recipient_id) is not None
    )
    messaging_enabled = True
    if recipient_is_creator:
        profile = db.scalar(
            select(CreatorProfile).where(CreatorProfile.user_id == recipient_id)
        )
        messaging_enabled = profile is None or profile.allow_messages_from_all_followers

    # Reuse the exact gate logic: creators may always message subscribers;
    # subscribers need follower + policy (with the existing-thread carve-out).
    can_message = True
    reason = ""
    if recipient.id == user.id:
        can_message = False
        reason = "You cannot message yourself."
    elif user.role == UserRole.creator and recipient.role == UserRole.creator:
        can_message = False
        reason = "DMs are between a creator and their subscribers."
    elif user.role != UserRole.creator and not recipient_is_creator:
        can_message = False
        reason = "You can only send messages to creators."
    elif user.role != UserRole.creator and not has_conversation:
        if not is_follower:
            can_message = False
            reason = "Only followers can message this creator."
        elif not messaging_enabled:
            can_message = False
            reason = (
                "This creator has messaging turned off — you can only message "
                "them if you already have an existing conversation."
            )

    return MessagesStatusOut(
        recipient_id=recipient.id,
        recipient_username=recipient.username,
        recipient_is_creator=recipient_is_creator,
        is_follower=is_follower,
        has_conversation=has_conversation,
        messaging_enabled=messaging_enabled,
        can_message=can_message,
        reason=reason,
    )


def _conversation_out(
    db: Session,
    service: MessageService,
    requester: User,
    conversation: Conversation,
) -> ConversationOut:
    other_id = (
        conversation.subscriber_id
        if conversation.creator_id == requester.id
        else conversation.creator_id
    )
    other = db.get(User, other_id)
    last = service.last_message(conversation.id)
    return ConversationOut(
        id=conversation.id,
        creator_id=conversation.creator_id,
        subscriber_id=conversation.subscriber_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        other=UserSummaryOut(
            id=other.id,
            username=other.username,
        ),
        last_message=last,
    )
