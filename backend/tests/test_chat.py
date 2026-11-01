"""Chat feature tests. The AI gateway is faked — tests cover persistence,
streaming event shape, and error paths, not the provider."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from httpx import AsyncClient
from openai import APIConnectionError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, UpstreamError
from app.features.chat import service
from app.features.chat.models import Message
from app.features.chat.router import chat_ws


class _FakeDelta:
    def __init__(self, content: str | None):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None):
        self.delta = _FakeDelta(content)


class _FakeChunk:
    def __init__(self, content: str | None):
        self.choices = [_FakeChoice(content)]


class _FakeStream:
    def __init__(self, tokens: list[str]):
        self._tokens = tokens

    def __aiter__(self):
        async def gen():
            for t in self._tokens:
                yield _FakeChunk(t)

        return gen()


class _FakeCompletions:
    def __init__(self, tokens: list[str]):
        self._tokens = tokens
        self.last_messages: list[dict] | None = None
        self.last_extra_body: dict | None = None

    async def create(
        self,
        *,
        model: str,
        messages: list[dict],
        stream: bool,
        extra_body: dict | None = None,
    ):
        self.last_messages = messages
        self.last_extra_body = extra_body
        return _FakeStream(self._tokens)


class _FakeChat:
    def __init__(self, tokens: list[str]):
        self.completions = _FakeCompletions(tokens)


class FakeAIClient:
    """Stand-in for AsyncOpenAI returning a canned token stream."""

    def __init__(self, tokens: list[str]):
        self.chat = _FakeChat(tokens)


class FakeWebSocket:
    """Stand-in for a Starlette WebSocket: scripted inbound frames, recorded
    sends. Dicts arrive as JSON text frames, str as raw text, bytes as
    binary; then it reports a disconnect like a real client."""

    def __init__(self, incoming: list[dict | str | bytes]):
        self.incoming = incoming
        self.sent: list[dict] = []
        self.accepted = False
        self.closed_code: int | None = None

    async def accept(self):
        self.accepted = True

    async def receive(self):
        if not self.incoming:
            return {"type": "websocket.disconnect", "code": 1000}
        item = self.incoming.pop(0)
        if isinstance(item, bytes):
            return {"type": "websocket.receive", "bytes": item}
        text = item if isinstance(item, str) else json.dumps(item)
        return {"type": "websocket.receive", "text": text}

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code: int = 1000):
        self.closed_code = code


@pytest.fixture
def fake_ai(monkeypatch):
    client = FakeAIClient(["Hello", ", ", "world"])
    monkeypatch.setattr(service, "get_ai_client", lambda: client)
    return client


async def _create_conversation(client: AsyncClient) -> int:
    resp = await client.post("/api/v1/chat/conversations", json={"title": ""})
    assert resp.status_code == 201
    return resp.json()["data"]["id"]


async def _collect_sse(client: AsyncClient, url: str, payload: dict) -> list[dict]:
    """POST and parse the SSE response body into {event, data} dicts."""
    resp = await client.post(url, json=payload)
    assert resp.status_code == 200
    events: list[dict] = []
    # sse-starlette frames are CRLF-separated; normalize before splitting.
    for block in resp.text.replace("\r\n", "\n").split("\n\n"):
        event: dict = {}
        for line in block.splitlines():
            if line.startswith("event:"):
                event["event"] = line[6:].strip()
            elif line.startswith("data:"):
                event["data"] = line[5:].strip()
        if event:
            events.append(event)
    return events


async def test_conversation_crud(client: AsyncClient):
    conv_id = await _create_conversation(client)

    resp = await client.get("/api/v1/chat/conversations")
    assert resp.json()["success"] is True
    assert any(c["id"] == conv_id for c in resp.json()["data"])

    resp = await client.delete(f"/api/v1/chat/conversations/{conv_id}")
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "data": None}

    resp = await client.get(f"/api/v1/chat/conversations/{conv_id}/messages")
    assert resp.status_code == 404


async def test_send_message_streams_and_persists(client: AsyncClient, fake_ai):
    conv_id = await _create_conversation(client)

    events = await _collect_sse(
        client,
        f"/api/v1/chat/conversations/{conv_id}/messages",
        {"content": "hi"},
    )
    tokens = [json.loads(e["data"])["token"] for e in events if e["event"] == "token"]
    assert tokens == ["Hello", ", ", "world"]

    final = [e for e in events if e["event"] == "message"]
    assert len(final) == 1
    assert json.loads(final[0]["data"])["content"] == "Hello, world"

    # Both user + assistant messages are persisted.
    resp = await client.get(f"/api/v1/chat/conversations/{conv_id}/messages")
    msgs = resp.json()["data"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "Hello, world"

    # The fake gateway saw the full history including the user message.
    assert fake_ai.chat.completions.last_messages[-1] == {
        "role": "user",
        "content": "hi",
    }
    # No fallbacks configured — nothing extra is sent to the gateway.
    assert fake_ai.chat.completions.last_extra_body is None

    # First user message titled the conversation.
    resp = await client.get("/api/v1/chat/conversations")
    conv = next(c for c in resp.json()["data"] if c["id"] == conv_id)
    assert conv["title"] == "hi"


async def test_send_message_forwards_configured_fallbacks(
    client: AsyncClient, fake_ai, monkeypatch
):
    monkeypatch.setattr(
        service.settings,
        "AI_CHAT_FALLBACKS",
        "anthropic/claude-3-5-sonnet-20241022, gemini/gemini-1.5-pro",
    )
    conv_id = await _create_conversation(client)
    await _collect_sse(
        client,
        f"/api/v1/chat/conversations/{conv_id}/messages",
        {"content": "hi"},
    )
    assert fake_ai.chat.completions.last_extra_body == {
        "fallbacks": [
            "anthropic/claude-3-5-sonnet-20241022",
            "gemini/gemini-1.5-pro",
        ]
    }


async def test_disconnect_persists_partial_reply(client: AsyncClient, fake_ai, db: AsyncSession):
    """Client abandons the stream mid-reply: what arrived is still saved."""
    conv_id = await _create_conversation(client)
    await service.send_user_message(db, conv_id, "hi")

    # httpx can't abandon an SSE response, so drive the generator directly:
    # take one token, then close — what a disconnect does under the hood.
    gen = service.stream_reply(conv_id, "hi", False)
    first = await gen.__anext__()
    assert json.loads(first["data"])["token"] == "Hello"
    await gen.aclose()

    assert service._background_persists
    await asyncio.gather(*service._background_persists)

    resp = await client.get(f"/api/v1/chat/conversations/{conv_id}/messages")
    msgs = resp.json()["data"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "Hello"


async def test_ws_streams_reply(client: AsyncClient, fake_ai):
    """The WS transport emits the same {event, data} frames as SSE and
    persists the reply — stream_reply doesn't know which transport ran."""
    conv_id = await _create_conversation(client)

    ws = FakeWebSocket([{"content": "hi"}])
    await chat_ws(ws, conv_id)

    assert ws.accepted
    tokens = [json.loads(e["data"])["token"] for e in ws.sent if e["event"] == "token"]
    assert tokens == ["Hello", ", ", "world"]
    final = [e for e in ws.sent if e["event"] == "message"]
    assert len(final) == 1
    assert json.loads(final[0]["data"])["content"] == "Hello, world"

    resp = await client.get(f"/api/v1/chat/conversations/{conv_id}/messages")
    assert [m["role"] for m in resp.json()["data"]] == ["user", "assistant"]


async def test_ws_invalid_frame_then_valid(client: AsyncClient, fake_ai):
    """A malformed frame earns an error frame; the connection stays usable."""
    conv_id = await _create_conversation(client)

    ws = FakeWebSocket([{"generate_image": False}, {"content": "hi"}])
    await chat_ws(ws, conv_id)

    assert ws.sent[0]["event"] == "error"
    tokens = [e for e in ws.sent if e["event"] == "token"]
    assert len(tokens) == 3  # the second, valid frame still streamed


async def test_ws_malformed_frames_keep_socket_usable(client: AsyncClient, fake_ai):
    """Non-JSON text and binary frames used to crash the handler (1011);
    now each earns an error frame and the next valid frame still streams."""
    conv_id = await _create_conversation(client)

    ws = FakeWebSocket(["not json", b"\x00\x01", {"content": "hi"}])
    await chat_ws(ws, conv_id)

    assert [e["event"] for e in ws.sent[:2]] == ["error", "error"]
    assert len([e for e in ws.sent if e["event"] == "token"]) == 3


async def test_delete_conversation_cascades_messages(client: AsyncClient, fake_ai, db):
    """Messages go with their conversation — via the FK's ON DELETE
    CASCADE (passive_deletes), so the ORM never loads them first."""
    conv_id = await _create_conversation(client)
    await _collect_sse(client, f"/api/v1/chat/conversations/{conv_id}/messages", {"content": "hi"})
    resp = await client.delete(f"/api/v1/chat/conversations/{conv_id}")
    assert resp.status_code == 200

    count = await db.scalar(
        select(func.count()).select_from(Message).where(Message.conversation_id == conv_id)
    )
    assert count == 0


async def test_ws_unknown_conversation_closes(client: AsyncClient):
    ws = FakeWebSocket([{"content": "hi"}])
    await chat_ws(ws, 9999)

    assert ws.sent[0]["event"] == "error"
    assert ws.closed_code == 4404


async def test_send_message_unknown_conversation(client: AsyncClient):
    resp = await client.post("/api/v1/chat/conversations/9999/messages", json={"content": "hi"})
    assert resp.status_code == 404
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_FOUND"


async def test_validation_error_envelope(client: AsyncClient):
    resp = await client.post("/api/v1/chat/conversations", json={"title": "x" * 201})
    assert resp.status_code == 422
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["errors"][0]["loc"] == ["body", "title"]


async def test_image_message_calls_image_path(client: AsyncClient, fake_ai, monkeypatch, tmp_path):
    saved: dict = {}

    async def fake_generate(prompt: str) -> bytes:
        return b"fakepng"

    async def fake_save(data: bytes, ext: str) -> str:
        saved["data"] = data
        return "/media/files/fake.png"

    monkeypatch.setattr(service.ai_media, "generate_image_bytes", fake_generate)
    monkeypatch.setattr(service, "save_media", fake_save)

    conv_id = await _create_conversation(client)
    events = await _collect_sse(
        client,
        f"/api/v1/chat/conversations/{conv_id}/messages",
        {"content": "a cat", "generate_image": True},
    )
    final = [e for e in events if e["event"] == "message"]
    assert len(final) == 1
    msg = json.loads(final[0]["data"])
    assert msg["media_url"] == "/media/files/fake.png"
    assert saved["data"] == b"fakepng"
    # The chat completion path was never touched.
    assert fake_ai.chat.completions.last_messages is None


@pytest.mark.parametrize("fail_at", ["generate", "save"])
async def test_image_failures_end_stream_with_error_event(
    client: AsyncClient, fake_ai, monkeypatch, fail_at
):
    """Upstream failures and unstorable results used to kill the SSE stream
    with no event; both now end it with an `error` event."""

    async def fake_generate(prompt: str) -> bytes:
        if fail_at == "generate":
            raise UpstreamError("image download failed")
        return b"fakepng"

    async def fake_save(data: bytes, ext: str) -> str:
        raise BadRequestError("media too large")

    monkeypatch.setattr(service.ai_media, "generate_image_bytes", fake_generate)
    monkeypatch.setattr(service, "save_media", fake_save)

    conv_id = await _create_conversation(client)
    events = await _collect_sse(
        client,
        f"/api/v1/chat/conversations/{conv_id}/messages",
        {"content": "a cat", "generate_image": True},
    )
    assert [e["event"] for e in events] == ["error"]


async def test_upstream_failure_mid_stream_persists_partial(client: AsyncClient, monkeypatch):
    """A gateway failure mid-reply keeps the tokens that arrived and ends
    the stream with an `error` event."""

    class _FailingStream:
        def __aiter__(self):
            async def gen():
                yield _FakeChunk("Hel")
                raise APIConnectionError(request=httpx.Request("POST", "http://gateway.test"))

            return gen()

    async def _create(**kwargs):
        return _FailingStream()

    monkeypatch.setattr(
        service,
        "get_ai_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create))),
    )
    conv_id = await _create_conversation(client)
    events = await _collect_sse(
        client, f"/api/v1/chat/conversations/{conv_id}/messages", {"content": "hi"}
    )
    assert [e["event"] for e in events] == ["token", "error"]
    resp = await client.get(f"/api/v1/chat/conversations/{conv_id}/messages")
    assert [(m["role"], m["content"]) for m in resp.json()["data"]] == [
        ("user", "hi"),
        ("assistant", "Hel"),
    ]
