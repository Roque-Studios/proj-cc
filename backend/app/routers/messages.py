"""DM endpoints: send a message, read conversations, serve message media.

``POST /messages`` delivers a creator-to-subscriber DM (1:1). The messaging
gate lives in ``MessageService``: a subscriber must be an active follower, and
a creator whose ``allow_messages_from_all_followers`` setting is off can only
be reached by followers who already have an **existing conversation** with
them — starting a new thread is blocked with a clear error.

Paid DM content: a message with ``price_cents`` set is a **paid message** —
the creator attaches media the recipient unlocks for a one-time price.
``POST /messages/with-media`` sends such a message (multipart);
``GET /messages/{id}/media`` serves a participant's media (watermarked,
locked until the one-time unlock); ``POST /messages/{id}/unlock`` creates the
hosted payment link (same checkout + webhook pattern as broadcasts).

``GET /conversations`` lists the requester's threads (with the other party and
a last-message preview); ``GET /conversations/{id}/messages`` is the message
history — participants only (an outsider gets the same 404 as a missing
thread, so conversation ids don't leak).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from .. import realtime
from ..access import is_active_follower, resolve_viewer_context
from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..media import (
    MediaValidationError,
    delete_original,
    save_original,
    serve_media,
    served_content_type,
    validate_upload,
)
from ..models import (
    Conversation,
    CreatorProfile,
    Message,
    MessageMedia,
    User,
    UserRole,
)
from ..payments import PaymentProviderError
from ..schemas import (
    ConversationOut,
    MessageOut,
    MessageSend,
    MessageUnlockResponse,
    MessagesPageOut,
    MessagesStatusOut,
    UserSummaryOut,
    build_message_out,
)
from ..services.gateways import resolve_unlock_provider
from ..services.messages import MessageGateError, MessageService, UnknownRecipientError
from ..services.paid_messages import PaidMessageNotPaidError, PaidMessageService
from ..storage import get_original_storage

router = APIRouter(tags=["messages"])

_CHUNK_SIZE = 64 * 1024


def _safe_return_url(url: str | None) -> str | None:
    """Return urls are handed to the payment gateway; only http(s) is allowed."""
    if url is None:
        return None
    if url.startswith(("http://", "https://")):
        return url
    return None


def _read_with_limit(upload: UploadFile, limit: int) -> bytes:
    """Read an upload fully, rejecting it once it exceeds ``limit`` bytes."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = upload.file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"File exceeds the {limit} byte size limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _unlock_state(message: Message, viewer_id: int, unlocked_ids: set[int]) -> bool | None:
    """The viewer's access to a message: None (free) / True / False (locked paid)."""
    if message.price_cents is None:
        return None
    if message.sender_id == viewer_id:
        return True  # the sender always has access to their own content
    return message.id in unlocked_ids


@router.post("/messages", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
async def send_message(
    payload: MessageSend,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send a text DM (JSON). Media uploads (and paid media content) use
    ``POST /messages/media``.

    Blocked with a clear 403 when the creator's policy forbids a new thread
    from this sender. Persists through the same gate as the WebSocket path,
    then pushes the message live to the recipient's connected sockets (any
    worker) via the realtime manager — so REST sends reach connected
    recipients in real time too, and the REST history is the fallback for
    anyone offline.
    """
    try:
        message = MessageService(db).send(
            user,
            payload.recipient_id,
            payload.body,
            price_cents=payload.price_cents,
        )
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

    out = build_message_out(message, _unlock_state(message, user.id, set()))
    payload_out = out.model_dump(mode="json")
    await realtime.manager.send_to_user(
        message.recipient_id,
        {"type": "message", "message": payload_out},
        message_id=message.id,
    )
    return out


@router.post(
    "/messages/media", response_model=MessageOut, status_code=status.HTTP_201_CREATED
)
async def send_message_with_media(
    recipient_id: int = Form(...),
    body: str = Form(default=""),
    price_cents: int | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send a DM with image uploads (multipart), optionally as one-time
    **paid content** (``price_cents`` — the recipient unlocks it with a hosted
    one-time payment, see ``POST /messages/{id}/unlock``).

    Files are validated and stored (the original is never served — every
    fetch is a per-viewer watermarked render); a failed persist cleans the
    uploads up. Sender policy: only a creator may set a price.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one media file is required",
        )
    if price_cents is not None and user.role != UserRole.creator:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only creators can send paid content",
        )

    media_rows: list[tuple[str, str]] = []
    saved_keys: list[str] = []
    try:
        for upload in files:
            data = _read_with_limit(upload, settings.MAX_MEDIA_SIZE_BYTES)
            media_type = validate_upload(
                upload.filename or "", upload.content_type or "", data
            )
            suffix = Path(upload.filename or "").suffix.lower()
            key = f"{uuid.uuid4().hex}{suffix}"
            save_original(data, key)
            media_rows.append((key, media_type))
            saved_keys.append(key)
    except MediaValidationError as exc:
        for key in saved_keys:
            delete_original(key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except HTTPException:
        # _read_with_limit raises 413 for oversized uploads.
        for key in saved_keys:
            delete_original(key)
        raise

    try:
        message = MessageService(db).send(
            user,
            recipient_id,
            body,
            price_cents=price_cents,
            media=media_rows,
        )
    except MessageGateError as exc:
        for key in saved_keys:
            delete_original(key)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except UnknownRecipientError as exc:
        for key in saved_keys:
            delete_original(key)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except ValueError as exc:
        for key in saved_keys:
            delete_original(key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception:
        for key in saved_keys:
            delete_original(key)
        raise

    out = build_message_out(message, _unlock_state(message, user.id, set()))
    payload_out = out.model_dump(mode="json")
    await realtime.manager.send_to_user(
        message.recipient_id,
        {"type": "message", "message": payload_out},
        message_id=message.id,
    )
    return out


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

    # Paid messages report the viewer's lock/unlock state (the sender always
    # has access; a subscriber needs the one-time unlock).
    unlocked_ids = PaidMessageService(db).unlocked_message_ids(
        user.id, [m.id for m in rows]
    )
    return MessagesPageOut(
        messages=[
            build_message_out(m, _unlock_state(m, user.id, unlocked_ids))
            for m in reversed(rows)
        ],
        before_id=oldest_loaded,
        has_more=has_more,
    )


@router.api_route("/messages/{message_id}/media", methods=["GET", "HEAD"])
def serve_message_media(
    message_id: int,
    request: Request,
    media_id: int = Query(..., description="Id of the media file within the message"),
    db: Session = Depends(get_db),
):
    """Serve one of a message's media files to a participant, watermarked.

    Authenticates (Bearer header or ``?token=`` for ``<img>`` tags),
    authorizes (only the conversation's two participants — an outsider gets
    the same 404 as a missing message), then serves the per-viewer
    watermarked bytes. Paid messages stay locked (403) until the one-time
    unlock is paid; the sender always has access. The original unwatermarked
    file is never exposed.
    """
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )

    ctx = resolve_viewer_context(request, message.sender_id, db)
    if ctx.user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if ctx.user.id not in (message.sender_id, message.recipient_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )
    if (
        message.price_cents is not None
        and ctx.user.id != message.sender_id
        and not PaidMessageService(db).is_unlocked(ctx.user.id, message.id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Paid message is locked — one-time unlock required",
        )

    media = db.get(MessageMedia, media_id)
    if media is None or media.message_id != message_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media not found",
        )

    storage = get_original_storage()
    if not storage.exists(media.storage_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media file missing",
        )

    storage_key = media.storage_key
    media_type = media.media_type
    user_ref = f"user:{ctx.user.id}"
    watermarked, cache_status = serve_media(storage_key, user_ref, post_id=message.id)
    return Response(
        content=watermarked,
        media_type=served_content_type(media_type),
        headers={
            "Cache-Control": "no-store",
            "X-Watermark": user_ref,
            "X-Watermark-Cache": cache_status,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/messages/{message_id}/unlock", response_model=MessageUnlockResponse)
def unlock_paid_message(
    message_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    success_url: str | None = Query(default=None),
    cancel_url: str | None = Query(default=None),
):
    """Create (or resume) the hosted checkout for a paid message.

    The recipient pays once on the gateway's page; the payment webhook
    activates the unlock. Idempotent: an already-paid message returns
    ``already_unlocked`` with no checkout url; a pending row re-surfaces its
    link; the sender is always "unlocked".
    """
    message = db.get(Message, message_id)
    if message is None or user.id not in (message.sender_id, message.recipient_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )
    if message.price_cents is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This message is not a paid message",
        )
    if user.id == message.sender_id:
        return MessageUnlockResponse(
            message_id=message.id,
            price_cents=message.price_cents,
            already_unlocked=True,
        )
    try:
        # Only creators can send paid content, so the sender is the creator —
        # use their enabled gateway (same account + webhook secret as their
        # subscription checkout), with a settings fallback for the mock/dev path.
        service = PaidMessageService(
            db, provider=resolve_unlock_provider(db, message.sender_id)
        )
        _, _, checkout_url = service.create_unlock(
            user.id,
            message,
            success_url=_safe_return_url(success_url),
            cancel_url=_safe_return_url(cancel_url),
        )
    except PaidMessageNotPaidError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except PaymentProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Payment provider error: {exc}",
        )
    return MessageUnlockResponse(
        message_id=message.id,
        price_cents=message.price_cents,
        already_unlocked=checkout_url is None,
        checkout_url=checkout_url,
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
    # Surface the creator's avatar so the chat shows a real picture instead of
    # a blank initials circle; subscribers have no avatar upload (None).
    avatar_url = None
    if other is not None and other.role == UserRole.creator:
        profile = db.scalar(
            select(CreatorProfile).where(CreatorProfile.user_id == other.id)
        )
        avatar_url = profile.avatar_url if profile is not None else None
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
            avatar_url=avatar_url,
        ),
        last_message=last,
    )
