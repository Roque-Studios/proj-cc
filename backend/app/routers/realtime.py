"""Real-time DM WebSocket endpoint.

``WS /ws/dms?token=<access JWT>`` authenticates the connection (query token —
browsers can't send ``Authorization`` headers on WebSockets), then streams
frames:

- client → server: ``{"type": "send", "recipient_id": int, "body": str}`` —
  delivered through the **same DM gate** as ``POST /messages`` (follower +
  policy checks); also ``{"type": "ping"}`` keepalive.
- server → client: ``{"type": "ack", "message": {...}}`` after a send
  persists; ``{"type": "message", "message": {...}, "id": <message id>}`` when
  a new message arrives in one of the user's conversations (sent here or from
  another worker/device via the REST endpoint — the top-level ``id`` is a
  delivery-dedupe stamp, see ``app.realtime``); ``{"type": "pong"}``;
  ``{"type": "error", "detail": str}`` for gate rejections or malformed
  frames.

Delivery is local-first with a best-effort Redis relay (see ``app.realtime``):
a disconnected recipient simply fetches
``GET /conversations/{id}/messages`` on reconnect — the persisted REST layer
is the fallback.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from .. import realtime
from ..database import get_db
from ..deps import user_from_access_token
from ..models import User
from ..schemas import MessageOut
from ..services.messages import MessageGateError, MessageService, UnknownRecipientError

router = APIRouter(tags=["realtime"])


async def _frame(websocket: WebSocket, payload: dict) -> None:
    await websocket.send_text(json.dumps(payload))


@router.websocket("/ws/dms")
async def dm_socket(
    websocket: WebSocket,
    db: Session = Depends(get_db),
    token: str = Query(default=""),
):
    """Live DM connection: authenticated, then send/receive message frames."""
    user = user_from_access_token(token, db)
    if user is None:
        await websocket.close(code=4401)  # unauthorized
        return

    await websocket.accept()
    await realtime.manager.connect(user.id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            await _handle_frame(user, raw, db, websocket)
    except WebSocketDisconnect:
        pass  # normal client close
    finally:
        realtime.manager.disconnect(user.id, websocket)


async def _handle_frame(user: User, raw: str, db: Session, websocket: WebSocket) -> None:
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError:
        await _frame(websocket, {"type": "error", "detail": "Malformed JSON frame"})
        return

    frame_type = frame.get("type")
    if frame_type == "ping":
        await _frame(websocket, {"type": "pong"})
        return
    if frame_type != "send":
        await _frame(
            websocket,
            {"type": "error", "detail": f"Unknown frame type: {frame_type}"},
        )
        return

    recipient_id = frame.get("recipient_id")
    if not isinstance(recipient_id, int) or recipient_id <= 0:
        await _frame(websocket, {"type": "error", "detail": "recipient_id must be a positive integer"})
        return

    try:
        message = MessageService(db).send(user, recipient_id, str(frame.get("body", "")))
    except MessageGateError as exc:
        await _frame(websocket, {"type": "error", "detail": str(exc)})
        return
    except UnknownRecipientError as exc:
        await _frame(websocket, {"type": "error", "detail": str(exc)})
        return
    except ValueError as exc:
        await _frame(websocket, {"type": "error", "detail": str(exc)})
        return

    payload = MessageOut.model_validate(message).model_dump(mode="json")
    # The sender's ack — then push the same frame to the recipient's sockets
    # (live across every worker via the relay).
    await _frame(websocket, {"type": "ack", "message": payload})
    await realtime.manager.send_to_user(
        message.recipient_id,
        {"type": "message", "message": payload},
        message_id=message.id,
    )
