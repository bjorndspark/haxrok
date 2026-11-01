import json
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.db import get_db
from app.core.errors import NotFoundError
from app.core.responses import SuccessResponse, ok
from app.features.chat import service
from app.features.chat.schemas import (
    ConversationCreate,
    ConversationRead,
    MessageCreate,
    MessageRead,
)
from app.features.chat.voice import VoiceSession

router = APIRouter(prefix="/chat", tags=["chat"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate, db: DbDep
) -> SuccessResponse[ConversationRead]:
    conv = await service.create_conversation(db, body.title)
    return ok(ConversationRead.model_validate(conv))


@router.get("/conversations")
async def list_conversations(db: DbDep) -> SuccessResponse[list[ConversationRead]]:
    convs = await service.list_conversations(db)
    return ok([ConversationRead.model_validate(c) for c in convs])


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int, db: DbDep) -> SuccessResponse[None]:
    await service.delete_conversation(db, conversation_id)
    return ok(None)


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: int, db: DbDep) -> SuccessResponse[list[MessageRead]]:
    messages = await service.list_messages(db, conversation_id)
    return ok([MessageRead.model_validate(m) for m in messages])


@router.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: int, body: MessageCreate, db: DbDep) -> EventSourceResponse:
    """Send a user message; the response is an SSE stream of the assistant reply.
    Streams are exempt from the envelope — see app.core.responses."""
    await service.send_user_message(db, conversation_id, body.content)
    # The request session can't live across the stream — it would pin a pool
    # connection for the stream's whole lifetime. stream_reply opens its own
    # short-lived sessions, so release this one before streaming starts.
    await db.close()
    return EventSourceResponse(
        service.stream_reply(conversation_id, body.content, body.generate_image)
    )


@router.websocket("/conversations/{conversation_id}/ws")
async def chat_ws(websocket: WebSocket, conversation_id: int) -> None:
    """WebSocket transport for the same reply stream as POST .../messages.

    Frames in:  {"content": "...", "generate_image": false}
    Frames out: {"event": "token"|"message"|"error", "data": <json string>} —
    the exact dicts stream_reply yields, so both transports share one wire
    contract and one client-side event parser.

    No request-scoped session exists here: connections outlive requests, so
    each inbound frame gets its own short-lived session. Errors are reported
    as `error` frames (a protocol has no status codes) and the socket stays
    usable; a missing conversation is terminal — the socket closes with app
    code 4404."""
    await websocket.accept()

    async def send_error(message: str) -> None:
        await websocket.send_json({"event": "error", "data": json.dumps({"message": message})})

    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if (raw := msg.get("text")) is None:
                await send_error("expected a JSON text frame")
                continue
            try:
                # model_validate_json rejects malformed JSON and bad shapes alike.
                body = MessageCreate.model_validate_json(raw)
            except ValidationError as e:
                await send_error(str(e))
                continue
            try:
                await service.record_user_message(conversation_id, body.content)
            except NotFoundError as e:
                await send_error(e.detail)
                await websocket.close(code=4404)
                return
            async for event in service.stream_reply(
                conversation_id, body.content, body.generate_image
            ):
                await websocket.send_json(event)
    except WebSocketDisconnect:
        pass


@router.websocket("/conversations/{conversation_id}/voice")
async def voice_ws(websocket: WebSocket, conversation_id: int) -> None:
    """Voice-mode transport: PCM mic audio in, transcript + reply + TTS
    audio out, all on one socket. See features/chat/voice.py for the frame
    protocol. Non-JSON frames are binary — the envelope rules don't apply."""
    await websocket.accept()
    session = VoiceSession(websocket, conversation_id)
    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if (chunk := msg.get("bytes")) is not None:
                session.feed_pcm(chunk)
            elif (raw := msg.get("text")) is not None:
                await session.handle_control(raw)
    except WebSocketDisconnect:
        pass
    finally:
        await session.aclose()
