"""Chat orchestration: conversation CRUD, user-message persistence, and the
streamed assistant reply. Owns every commit — the repository only flushes.
Image requests branch to the image endpoint and reply with a media_url
instead of streamed text.

SSE event shapes (consumed by frontend features/chat/api.ts):
  event: token    data: {"token": "..."}          — streamed text chunk
  event: message  data: <MessageRead JSON>        — persisted assistant reply
  event: error    data: {"message": "..."}        — upstream failure mid-stream
"""

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from typing import cast

from openai import APIError
from openai.types.chat import ChatCompletionMessageParam
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai import media as ai_media
from app.core.ai.client import fallbacks_extra_body, get_ai_client
from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.core.errors import AppError, NotFoundError, UpstreamError
from app.core.logging import get_logger
from app.core.storage import save_media
from app.features.chat import repository
from app.features.chat.models import Conversation, Message
from app.features.chat.schemas import MessageRead

logger = get_logger(__name__)

# Partial replies persisted after a client disconnect run as detached tasks —
# the streaming request's task is being torn down and can't host the write.
_background_persists: set[asyncio.Task] = set()


# --- conversations -----------------------------------------------------------


async def create_conversation(db: AsyncSession, title: str = "") -> Conversation:
    conv = await repository.create_conversation(db, title)
    await db.commit()
    return conv


async def list_conversations(db: AsyncSession) -> list[Conversation]:
    return await repository.list_conversations(db)


async def get_conversation(db: AsyncSession, conversation_id: int) -> Conversation:
    """Fetch a conversation or raise NotFoundError."""
    conv = await repository.get_conversation(db, conversation_id)
    if conv is None:
        raise NotFoundError(f"conversation {conversation_id} not found")
    return conv


async def delete_conversation(db: AsyncSession, conversation_id: int) -> None:
    conv = await get_conversation(db, conversation_id)
    await repository.delete_conversation(db, conv)
    await db.commit()


async def list_messages(db: AsyncSession, conversation_id: int) -> list[Message]:
    await get_conversation(db, conversation_id)
    return await repository.list_messages(db, conversation_id)


# --- user messages -----------------------------------------------------------


async def send_user_message(db: AsyncSession, conversation_id: int, content: str) -> None:
    """Persist the user message. Runs eagerly in the router so a missing
    conversation is a real 404 — once the SSE response starts, errors can't
    change the status code."""
    conv = await get_conversation(db, conversation_id)
    await _add_user_message(db, conv, content)
    await db.commit()


async def record_user_message(conversation_id: int, content: str) -> None:
    """send_user_message for transports with no request scope (WebSocket):
    opens and closes its own session per inbound frame."""
    async with AsyncSessionLocal() as db:
        await send_user_message(db, conversation_id, content)


async def record_exchange(
    conversation_id: int, user_content: str, reply: str | None
) -> Message | None:
    """Persist a voice turn — the user message and its (possibly partial)
    reply — in one transaction. Voice calls this only once the utterance
    is confirmed, so a cancelled speculation leaves no rows behind.
    ``reply=None`` saves the user message alone; returns the assistant
    message, if any."""
    async with AsyncSessionLocal() as db:
        conv = await get_conversation(db, conversation_id)
        await _add_user_message(db, conv, user_content)
        msg = None
        if reply is not None:
            msg = await repository.add_message(db, conversation_id, "assistant", reply)
        await db.commit()
        return msg


async def _add_user_message(db: AsyncSession, conv: Conversation, content: str) -> None:
    await repository.add_message(db, conv.id, "user", content)
    # First message titles the conversation — cheap heuristic, no extra LLM call.
    if not conv.title:
        conv.title = content[:80]


# --- assistant reply ---------------------------------------------------------


async def load_history(conversation_id: int) -> list[ChatCompletionMessageParam]:
    """The conversation so far as chat-completion messages, on a short-lived
    session of its own. Image replies are skipped — they carry a media_url,
    not conversational text. Raises NotFoundError."""
    async with AsyncSessionLocal() as db:
        await get_conversation(db, conversation_id)
        history = await repository.list_messages(db, conversation_id)
    # role comes back from the DB as plain str; only "user"/"assistant" are
    # ever written, so the cast is safe.
    return [
        cast(ChatCompletionMessageParam, {"role": m.role, "content": m.content})
        for m in history
        if not m.media_url
    ]


async def stream_tokens(
    messages: list[ChatCompletionMessageParam],
) -> AsyncGenerator[str, None]:
    """Stream the assistant reply's text deltas from the gateway. Raises
    UpstreamError on gateway/provider failure, before or mid-stream."""
    try:
        stream = await get_ai_client().chat.completions.create(
            model=settings.AI_CHAT_MODEL,
            messages=messages,
            stream=True,
            extra_body=fallbacks_extra_body(settings.AI_CHAT_FALLBACKS),
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta
    except APIError as e:
        logger.warning("chat completion failed: %s", e)
        raise UpstreamError(e.message) from e


async def stream_reply(
    conversation_id: int, content: str, generate_image: bool
) -> AsyncIterator[dict]:
    """Yield SSE event dicts for the assistant reply. Assumes the user message
    is already persisted (send_user_message ran in the router).

    Deliberately session-free: the request-scoped session is closed before
    streaming starts (holding it would pin a pool connection for the stream's
    whole lifetime, and its teardown on disconnect would kill the final
    persist). History reads and writes open short-lived sessions instead."""
    if generate_image:
        async for event in _image_reply(conversation_id, content):
            yield event
        return

    parts: list[str] = []
    try:
        history = await load_history(conversation_id)
        async with aclosing(stream_tokens(history)) as tokens:
            async for delta in tokens:
                parts.append(delta)
                yield _event("token", {"token": delta})
    except (asyncio.CancelledError, GeneratorExit):
        # The client went away mid-reply (SSE disconnect, WS close) and this
        # generator is being torn down — the partial reply is persisted by a
        # detached task, so it survives a reload instead of vanishing.
        if parts:
            _persist_detached(conversation_id, "".join(parts))
        raise
    except AppError as e:
        if parts:
            await _persist_reply(conversation_id, "".join(parts))
        yield _event("error", {"message": e.detail})
        return

    msg = await _persist_reply(conversation_id, "".join(parts))
    yield {"event": "message", "data": MessageRead.model_validate(msg).model_dump_json()}


def _event(name: str, data: dict) -> dict:
    return {"event": name, "data": json.dumps(data)}


async def _persist_reply(
    conversation_id: int, content: str, media_url: str | None = None
) -> Message:
    """Write the assistant message on its own session — never the request's."""
    async with AsyncSessionLocal() as db:
        msg = await repository.add_message(
            db, conversation_id, "assistant", content, media_url=media_url
        )
        await db.commit()
        return msg


def _persist_detached(conversation_id: int, content: str) -> None:
    """Fire-and-forget persist for a reply whose client disconnected. The set
    keeps a reference so the task isn't GC'd before it finishes."""
    task = asyncio.create_task(_persist_reply(conversation_id, content))
    _background_persists.add(task)
    task.add_done_callback(_background_persists.discard)


async def _image_reply(conversation_id: int, prompt: str) -> AsyncIterator[dict]:
    try:
        data = await ai_media.generate_image_bytes(prompt)
        url = await save_media(data, "png")
    except AppError as e:
        # Upstream failure or an unstorable result — either way the stream
        # must end with an error event, not a dropped connection.
        yield _event("error", {"message": e.detail})
        return
    msg = await _persist_reply(conversation_id, f"Generated image: {prompt}", media_url=url)
    yield {"event": "message", "data": MessageRead.model_validate(msg).model_dump_json()}
