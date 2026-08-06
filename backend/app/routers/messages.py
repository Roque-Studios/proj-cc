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

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import realtime
from ..database import get_db
from ..deps import get_current_user
from ..models import Conversation, Message, User
from ..schemas import (
    ConversationOut,
    MessageOut,
    MessageSend,
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


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def conversation_messages(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A thread's message history, oldest first. Participants only."""
    conversation = db.get(Conversation, conversation_id)
    if (
        conversation is None
        or user.id not in (conversation.creator_id, conversation.subscriber_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id)
    ).all()


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
