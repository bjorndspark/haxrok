"""Voice-mode tests. The AI gateway is faked — tests cover the wire
protocol, speculative-turn lifecycle, chunk splitting, and persistence,
not the provider."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from app.features.chat import service, voice
from app.features.chat.router import voice_ws
from tests.test_chat import FakeAIClient, _create_conversation, _FakeChunk


class FakeVoiceWebSocket:
    """Stand-in for a Starlette WebSocket over websocket.receive(): a
    scripted inbound message list (text and bytes frames), then disconnect.
    Outbound JSON and binary sends land in one ordered list."""

    def __init__(self, incoming: list[dict]):
        self.incoming = incoming
        self.sent: list[dict | bytes] = []
        self.accepted = False
        self.closed_code: int | None = None

    async def accept(self):
        self.accepted = True

    async def receive(self):
        if self.incoming:
            return self.incoming.pop(0)
        # A real client stays connected while the reply plays out — hold
        # the socket open until the turn ends (or the server closes), else
        # aclose() cancels the reply task before it can emit anything.
        for _ in range(400):
            if self.closed_code is not None or any(
                isinstance(e, dict) and e.get("event") == "turn_end" for e in self.sent
            ):
                break
            await asyncio.sleep(0.005)
        return {"type": "websocket.disconnect"}

    async def send_json(self, data):
        self.sent.append(data)

    async def send_bytes(self, data):
        self.sent.append(data)

    async def close(self, code: int = 1000):
        self.closed_code = code


def _pcm(n_bytes: int) -> dict:
    return {"type": "websocket.receive", "bytes": b"\x00" * n_bytes}


def _ctrl(t: str) -> dict:
    return {"type": "websocket.receive", "text": json.dumps({"type": t})}


@pytest.fixture
def fake_voice_gateway(monkeypatch):
    """Fakes the two media calls voice.py makes: transcribe returns the
    next scripted (text, language) pair (optionally after a delay, so
    tests can cancel the call mid-flight); TTS yields one chunk prefixed
    with the negotiated format so tests can see which path ran."""
    state = {
        "transcripts": [],
        "stt_calls": 0,
        "stt_languages": [],
        "tts_calls": [],
        "stt_delay": 0.0,
    }

    async def fake_transcribe(data: bytes, filename: str, language=None):
        assert data[:4] == b"RIFF"  # utterances go up as WAV files
        state["stt_calls"] += 1
        state["stt_languages"].append(language)
        if state["stt_delay"]:
            await asyncio.sleep(state["stt_delay"])
        item = state["transcripts"].pop(0) if state["transcripts"] else ""
        text, lang = item if isinstance(item, tuple) else (item, "en")
        return text, lang

    async def fake_synth(text: str, fmt: str):
        state["tts_calls"].append((text, fmt))
        yield fmt.encode() + b":" + text.encode()[:8]

    monkeypatch.setattr(voice.ai_media, "transcribe_audio_verbose", fake_transcribe)
    monkeypatch.setattr(voice.ai_media, "synthesize_speech_stream", fake_synth)
    # Count any audio as enough for a turn.
    monkeypatch.setattr(voice, "MIN_STT_BYTES", 1)
    return state


@pytest.fixture
def fake_ai(monkeypatch):
    client = FakeAIClient(["Hello", ", ", "world"])
    monkeypatch.setattr(service, "get_ai_client", lambda: client)
    return client


def _events(ws: FakeVoiceWebSocket, name: str) -> list[dict]:
    return [e for e in ws.sent if isinstance(e, dict) and e["event"] == name]


async def _until(pred, timeout: float = 2.0) -> None:
    """Spin the loop until ``pred()`` holds — for steps that complete in
    background tasks (STT, streaming, release)."""
    for _ in range(int(timeout / 0.005)):
        if pred():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached")


async def _messages(client: AsyncClient, conv_id: int) -> list[tuple[str, str]]:
    resp = await client.get(f"/api/v1/chat/conversations/{conv_id}/messages")
    return [(m["role"], m["content"]) for m in resp.json()["data"]]


# --- SpeechChunker ------------------------------------------------------


def test_chunker_cuts_at_sentence_end():
    c = voice.SpeechChunker()
    assert c.feed("Hello there.") == ["Hello there."]
    assert c.feed(" Next one") == []
    assert c.flush() == "Next one"


def test_chunker_short_clause_merges_then_cuts():
    c = voice.SpeechChunker()
    # "Yes," is too short to speak alone — it merges forward and the
    # fragment cuts at the next clause boundary once it's long enough.
    assert c.feed("Yes, ") == []
    assert c.feed("I think so, and more besides") == ["Yes, I think so,"]
    assert c.feed(".") == ["and more besides."]
    assert c.flush() is None


def test_chunker_clause_boundary_cuts_long_fragment():
    c = voice.SpeechChunker()
    out = c.feed("This is a fairly long clause, ")
    assert out == ["This is a fairly long clause,"]


def test_chunker_hard_cut_runon():
    c = voice.SpeechChunker()
    out = c.feed("x" * (voice.HARD_CUT_CHARS + 10))
    assert out == ["x" * voice.HARD_CUT_CHARS]


def test_chunker_japanese_punctuation():
    c = voice.SpeechChunker()
    assert c.feed("こんにちは。") == ["こんにちは。"]


# --- wav_wrap ------------------------------------------------------------


def test_wav_wrap_header():
    wav = voice.wav_wrap(b"\x00" * 100)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert len(wav) == 44 + 100
    assert int.from_bytes(wav[4:8], "little") == 36 + 100


# --- WS session ----------------------------------------------------------


async def test_voice_turn_end_to_end(client: AsyncClient, fake_ai, fake_voice_gateway):
    """pcm frames + speech_end → transcript, tokens, ordered audio clip,
    persisted messages, turn_end. One STT call for the whole utterance."""
    conv_id = await _create_conversation(client)
    fake_voice_gateway["transcripts"] = ["hello world"]

    ws = FakeVoiceWebSocket([_ctrl("speech_start"), _pcm(16000), _pcm(16000), _ctrl("speech_end")])
    await voice_ws(ws, conv_id)

    assert ws.accepted
    assert fake_voice_gateway["stt_calls"] == 1
    final = _events(ws, "transcript")
    assert json.loads(final[0]["data"])["text"] == "hello world"

    tokens = [json.loads(e["data"])["token"] for e in _events(ws, "token")]
    assert tokens == ["Hello", ", ", "world"]

    # One clip: begin → binary chunk(s) → end, contiguous and in order.
    kinds = [
        "bin" if isinstance(e, bytes) else e["event"]
        for e in ws.sent
        if e == b"" or isinstance(e, bytes) or e["event"] in ("audio_begin", "audio_end")
    ]
    assert kinds == ["audio_begin", "bin", "audio_end"]
    begin = _events(ws, "audio_begin")[0]
    meta = json.loads(begin["data"])
    assert meta["format"] == "pcm" and meta["sample_rate"] == voice.TTS_PCM_SAMPLE_RATE
    assert fake_voice_gateway["tts_calls"] == [("Hello, world", "pcm")]

    assert len(_events(ws, "message")) == 1
    assert _events(ws, "turn_end")

    resp = await client.get(f"/api/v1/chat/conversations/{conv_id}/messages")
    msgs = resp.json()["data"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hello world"


async def test_voice_maybe_end_starts_speculative_turn(
    client: AsyncClient, fake_ai, fake_voice_gateway
):
    """speech_maybe_end runs the turn on the snapshot; the confirming
    speech_end does NOT start a second turn."""
    conv_id = await _create_conversation(client)
    fake_voice_gateway["transcripts"] = ["hello world"]

    ws = FakeVoiceWebSocket(
        [
            _ctrl("speech_start"),
            _pcm(16000),
            _ctrl("speech_maybe_end"),
            _pcm(4000),  # trailing silence keeps arriving during the hold
            _ctrl("speech_end"),
        ]
    )
    await voice_ws(ws, conv_id)

    assert fake_voice_gateway["stt_calls"] == 1
    assert _events(ws, "turn_end")
    resp = await client.get(f"/api/v1/chat/conversations/{conv_id}/messages")
    assert len(resp.json()["data"]) == 2  # exactly one user + one assistant


async def test_voice_speech_continue_cancels_speculation(
    client: AsyncClient, fake_ai, fake_voice_gateway
):
    """maybe_end → speech_continue (pause, not end) cancels the speculative
    turn unseen — no `interrupted`, nothing persisted; the real speech_end
    then runs the turn on the full buffer."""
    conv_id = await _create_conversation(client)
    # Same text either way — whether the cancelled call ran before the
    # cancel landed is scheduling-dependent and must not affect the result.
    fake_voice_gateway["transcripts"] = ["full utterance", "full utterance"]
    # Slow transcribe so the cancellation lands mid-call.
    fake_voice_gateway["stt_delay"] = 0.05

    ws = FakeVoiceWebSocket(
        [
            _ctrl("speech_start"),
            _pcm(16000),
            _ctrl("speech_maybe_end"),
            _ctrl("speech_continue"),
            _pcm(16000),
            _ctrl("speech_end"),
        ]
    )
    await voice_ws(ws, conv_id)

    assert not _events(ws, "interrupted")
    final = _events(ws, "transcript")
    assert [json.loads(e["data"])["text"] for e in final] == ["full utterance"]
    assert _events(ws, "turn_end")
    assert await _messages(client, conv_id) == [
        ("user", "full utterance"),
        ("assistant", "Hello, world"),
    ]


async def test_voice_fast_speculation_then_continue_is_one_utterance(
    client: AsyncClient, fake_ai, fake_voice_gateway, monkeypatch
):
    """Regression: a speculation whose STT (and reply) finished before
    speech_continue used to commit — the utterance split into two user
    messages with a reply in between. Now nothing persists or reaches the
    client until speech_end, and the next snapshot covers the whole
    utterance, pause included."""
    conv_id = await _create_conversation(client)
    fake_voice_gateway["transcripts"] = ["first half", "first half second half"]
    stt_sizes: list[int] = []
    real = voice.ai_media.transcribe_audio_verbose

    async def sized(data: bytes, filename: str, language=None):
        stt_sizes.append(len(data) - 44)
        return await real(data, filename, language)

    monkeypatch.setattr(voice.ai_media, "transcribe_audio_verbose", sized)
    ws = FakeVoiceWebSocket([])
    session = voice.VoiceSession(ws, conv_id)  # type: ignore[arg-type]

    await session.handle_control(_ctrl("speech_start")["text"])
    session.feed_pcm(b"\x00" * 16000)
    await session.handle_control(_ctrl("speech_maybe_end")["text"])
    # Let the speculation run to completion (STT + whole reply) while the
    # client's VAD is still holding.
    turn = session._turn
    assert turn is not None
    await _until(lambda: turn.pipe is not None and turn.pipe._closed)
    await asyncio.sleep(0.02)
    assert ws.sent == []  # held: the client has seen nothing
    assert await _messages(client, conv_id) == []

    await session.handle_control(_ctrl("speech_continue")["text"])
    session.feed_pcm(b"\x00" * 8000)
    await session.handle_control(_ctrl("speech_end")["text"])
    assert session._turn_task is not None
    await session._turn_task

    assert stt_sizes == [16000, 24000]  # second snapshot = whole utterance
    assert not _events(ws, "interrupted")
    assert [json.loads(e["data"])["text"] for e in _events(ws, "transcript")] == [
        "first half second half"
    ]
    assert await _messages(client, conv_id) == [
        ("user", "first half second half"),
        ("assistant", "Hello, world"),
    ]


async def test_voice_speech_cancel_drops_speculation(
    client: AsyncClient, fake_ai, fake_voice_gateway
):
    """Regression: a VAD misfire used to send speech_end, which ran a full
    turn on the blip (whisper's "Thank you." on noise). speech_cancel drops
    the utterance and its speculation unseen."""
    conv_id = await _create_conversation(client)
    fake_voice_gateway["transcripts"] = ["Thank you."]
    ws = FakeVoiceWebSocket([])
    session = voice.VoiceSession(ws, conv_id)  # type: ignore[arg-type]

    await session.handle_control(_ctrl("speech_start")["text"])
    session.feed_pcm(b"\x00" * 16000)
    await session.handle_control(_ctrl("speech_maybe_end")["text"])
    await _until(lambda: fake_voice_gateway["stt_calls"] == 1)
    await session.handle_control(_ctrl("speech_cancel")["text"])

    assert session._turn_task is None
    assert ws.sent == []
    assert await _messages(client, conv_id) == []
    # The next utterance starts from an empty buffer.
    assert len(session._pcm) == 0
    await session.aclose()


async def test_voice_speculation_released_on_speech_end(
    client: AsyncClient, fake_ai, fake_voice_gateway
):
    """Held frames go out in production order once speech_end confirms."""
    conv_id = await _create_conversation(client)
    fake_voice_gateway["transcripts"] = ["hello"]
    ws = FakeVoiceWebSocket([])
    session = voice.VoiceSession(ws, conv_id)  # type: ignore[arg-type]

    await session.handle_control(_ctrl("speech_start")["text"])
    session.feed_pcm(b"\x00" * 16000)
    await session.handle_control(_ctrl("speech_maybe_end")["text"])
    await _until(lambda: fake_voice_gateway["stt_calls"] == 1)
    await asyncio.sleep(0.02)
    assert ws.sent == []

    await session.handle_control(_ctrl("speech_end")["text"])
    assert session._turn_task is not None
    await session._turn_task

    order = [e["event"] if isinstance(e, dict) else "bin" for e in ws.sent]
    assert order[0] == "transcript"
    assert order[-1] == "turn_end"
    assert order.index("token") < order.index("message")
    assert "bin" in order


async def test_voice_interrupt_persists_partial_reply(
    client: AsyncClient, fake_voice_gateway, monkeypatch
):
    """Barge-in mid-reply: `interrupted` must not outrun the partial
    persist — the client refetches on interrupted, and a still-in-flight
    detached commit makes the reply appear to vanish from the chat."""
    conv_id = await _create_conversation(client)
    fake_voice_gateway["transcripts"] = ["hi"]

    # A reply stream that yields one token then blocks until cancelled —
    # the interrupt lands deterministically mid-stream.
    class _HangStream:
        def __aiter__(self):
            async def gen():
                yield _FakeChunk("Hello")
                await asyncio.Event().wait()  # the cancel lands here

            return gen()

    async def _create(**kwargs):
        return _HangStream()

    hang_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    monkeypatch.setattr(service, "get_ai_client", lambda: hang_client)

    ws = FakeVoiceWebSocket([])
    session = voice.VoiceSession(ws, conv_id)  # type: ignore[arg-type]

    await session.handle_control(_ctrl("speech_start")["text"])
    session.feed_pcm(b"\x00" * 16000)
    await session.handle_control(_ctrl("speech_end")["text"])
    # Wait until the reply is actually streaming before barging in —
    # a speech_start during STT cancels before any tokens exist.
    for _ in range(200):
        if _events(ws, "token"):
            break
        await asyncio.sleep(0.005)
    assert _events(ws, "token")

    await session.handle_control(_ctrl("speech_start")["text"])
    await session.aclose()

    assert _events(ws, "interrupted")
    # The interrupted turn's partial reply must already be committed —
    # the client invalidates messages as soon as `interrupted` lands.
    resp = await client.get(f"/api/v1/chat/conversations/{conv_id}/messages")
    msgs = resp.json()["data"]
    assert ("assistant", "Hello") in [(m["role"], m["content"]) for m in msgs]


async def test_voice_failed_speculation_answers_once_on_speech_end(
    client: AsyncClient, monkeypatch
):
    """A speculative turn whose STT fails holds its error until the
    utterance is confirmed, then answers speech_end with exactly one
    error + turn_end — the client is never left in "thinking"."""
    from app.core.errors import UpstreamError

    async def fail_transcribe(data: bytes, filename: str, language=None):
        raise UpstreamError("stt down")

    monkeypatch.setattr(voice.ai_media, "transcribe_audio_verbose", fail_transcribe)
    monkeypatch.setattr(voice, "MIN_STT_BYTES", 1)

    conv_id = await _create_conversation(client)
    ws = FakeVoiceWebSocket([])
    session = voice.VoiceSession(ws, conv_id)  # type: ignore[arg-type]

    await session.handle_control(_ctrl("speech_start")["text"])
    session.feed_pcm(b"\x00" * 16000)
    await session.handle_control(_ctrl("speech_maybe_end")["text"])
    await asyncio.sleep(0.02)
    assert ws.sent == []  # the failure is held with everything else

    await session.handle_control(_ctrl("speech_end")["text"])
    assert session._turn_task is not None
    await session._turn_task
    assert [e["event"] for e in ws.sent] == ["error", "turn_end"]


async def test_voice_repeat_maybe_end_does_not_respeculate(
    client: AsyncClient, fake_ai, monkeypatch
):
    """A noise-flapped VAD firing maybe_end twice inside one segment must
    not start a second turn on the same audio — the first speculation
    owns the utterance until speech_end/continue resolves it."""
    stt_sizes: list[int] = []

    async def fake_transcribe(data: bytes, filename: str, language=None):
        stt_sizes.append(len(data))
        return "noise", "en"

    async def fake_synth(text: str, fmt: str):
        yield b"X"

    monkeypatch.setattr(voice.ai_media, "transcribe_audio_verbose", fake_transcribe)
    monkeypatch.setattr(voice.ai_media, "synthesize_speech_stream", fake_synth)
    monkeypatch.setattr(voice, "MIN_STT_BYTES", 1)

    conv_id = await _create_conversation(client)
    ws = FakeVoiceWebSocket([])
    session = voice.VoiceSession(ws, conv_id)  # type: ignore[arg-type]

    await session.handle_control(_ctrl("speech_start")["text"])
    session.feed_pcm(b"\x00" * 16000)
    await session.handle_control(_ctrl("speech_maybe_end")["text"])
    session.feed_pcm(b"\x00" * 8000)
    await session.handle_control(_ctrl("speech_maybe_end")["text"])
    await session.handle_control(_ctrl("speech_end")["text"])
    assert session._turn_task is not None
    await session._turn_task

    # 44 = WAV header; one call, on the first snapshot.
    assert stt_sizes == [16000 + 44]
    assert [r for r, _ in await _messages(client, conv_id)] == ["user", "assistant"]


async def test_voice_next_utterance_is_not_retranscribed(client: AsyncClient, fake_ai, monkeypatch):
    """A finished turn's audio never reappears in the next utterance's
    snapshot (the repeated-transcript bug)."""
    stt_sizes: list[int] = []

    async def fake_transcribe(data: bytes, filename: str, language=None):
        stt_sizes.append(len(data) - 44)
        return "noise", "en"

    async def fake_synth(text: str, fmt: str):
        yield b"X"

    monkeypatch.setattr(voice.ai_media, "transcribe_audio_verbose", fake_transcribe)
    monkeypatch.setattr(voice.ai_media, "synthesize_speech_stream", fake_synth)
    monkeypatch.setattr(voice, "MIN_STT_BYTES", 1)

    conv_id = await _create_conversation(client)
    ws = FakeVoiceWebSocket([])
    session = voice.VoiceSession(ws, conv_id)  # type: ignore[arg-type]
    for size in (16000, 8000):
        await session.handle_control(_ctrl("speech_start")["text"])
        session.feed_pcm(b"\x00" * size)
        await session.handle_control(_ctrl("speech_end")["text"])
        assert session._turn_task is not None
        await session._turn_task

    assert stt_sizes == [16000, 8000]


async def test_voice_persist_failure_still_ends_turn(
    client: AsyncClient, fake_ai, fake_voice_gateway, monkeypatch
):
    """A failed write must still close the turn — error + turn_end."""
    conv_id = await _create_conversation(client)
    fake_voice_gateway["transcripts"] = ["hi"]

    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(service, "record_exchange", boom)
    ws = FakeVoiceWebSocket([_ctrl("speech_start"), _pcm(16000), _ctrl("speech_end")])
    await voice_ws(ws, conv_id)

    events = [e["event"] for e in ws.sent if isinstance(e, dict)]
    assert "error" in events
    assert events[-1] == "turn_end"


async def test_voice_maybe_end_ignored_without_speech(client: AsyncClient, fake_voice_gateway):
    """A stray speech_maybe_end while not recording does nothing."""
    conv_id = await _create_conversation(client)
    ws = FakeVoiceWebSocket([_ctrl("speech_maybe_end"), _pcm(16000)])
    await voice_ws(ws, conv_id)
    assert fake_voice_gateway["stt_calls"] == 0


async def test_voice_empty_transcript_no_turn(client: AsyncClient, fake_ai, fake_voice_gateway):
    """Nothing recognized → transcript + turn_end, no LLM call, no writes."""
    conv_id = await _create_conversation(client)
    ws = FakeVoiceWebSocket([_ctrl("speech_start"), _pcm(16000), _ctrl("speech_end")])
    await voice_ws(ws, conv_id)

    transcript = json.loads(_events(ws, "transcript")[0]["data"])["text"]
    assert transcript == ""
    assert _events(ws, "turn_end")
    assert not _events(ws, "message")
    assert fake_ai.chat.completions.last_messages is None


async def test_voice_mp3_fallback_when_pcm_rejected(client: AsyncClient, fake_ai, monkeypatch):
    """A gateway that rejects pcm flips the session to mp3 for the retry
    and all later clips."""
    from app.core.errors import UpstreamError

    async def fake_transcribe(data: bytes, filename: str, language=None):
        return "hi", "en"

    calls: list[str] = []

    async def fake_synth(text: str, fmt: str):
        calls.append(fmt)
        if fmt == "pcm":
            raise UpstreamError("unsupported format")
        yield b"MP3DATA"

    monkeypatch.setattr(voice.ai_media, "transcribe_audio_verbose", fake_transcribe)
    monkeypatch.setattr(voice.ai_media, "synthesize_speech_stream", fake_synth)
    monkeypatch.setattr(voice, "MIN_STT_BYTES", 1)

    conv_id = await _create_conversation(client)
    ws = FakeVoiceWebSocket([_ctrl("speech_start"), _pcm(16000), _ctrl("speech_end")])
    await voice_ws(ws, conv_id)

    assert calls == ["pcm", "mp3"]
    begin = _events(ws, "audio_begin")[0]
    assert json.loads(begin["data"])["format"] == "mp3"


async def test_voice_unknown_control_and_interrupt(client: AsyncClient, fake_voice_gateway):
    conv_id = await _create_conversation(client)
    ws = FakeVoiceWebSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"bogus"}'},
            {"type": "websocket.receive", "text": "not json"},
            # Valid JSON but not an object — used to crash the session.
            {"type": "websocket.receive", "text": "[1, 2]"},
            _ctrl("interrupt"),
        ]
    )
    await voice_ws(ws, conv_id)

    events = [e["event"] for e in ws.sent if isinstance(e, dict)]
    assert events == ["error", "error", "error", "interrupted"]


async def test_voice_unknown_conversation_closes(client: AsyncClient, fake_voice_gateway):
    """A transcript for a deleted conversation is an error frame + 4404 —
    the same terminal signal chat_ws uses."""
    fake_voice_gateway["transcripts"] = ["hi"]
    ws = FakeVoiceWebSocket([_ctrl("speech_start"), _pcm(16000), _ctrl("speech_end")])
    await voice_ws(ws, 9999)

    assert any(isinstance(e, dict) and e["event"] == "error" for e in ws.sent)
    assert ws.closed_code == 4404


async def test_voice_language_selection(client: AsyncClient, fake_ai, fake_voice_gateway):
    """speech_start's optional "language" pins the recognizer for the turn
    and is echoed back on the transcript event."""
    conv_id = await _create_conversation(client)
    fake_voice_gateway["transcripts"] = ["こんにちは"]
    ws = FakeVoiceWebSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"speech_start","language":"ja"}'},
            _pcm(16000),
            _ctrl("speech_end"),
        ]
    )
    await voice_ws(ws, conv_id)

    assert fake_voice_gateway["stt_languages"] == ["ja"]
    transcript = _events(ws, "transcript")[0]
    assert json.loads(transcript["data"])["language"] == "ja"


async def test_voice_auto_language_pins_detection(client: AsyncClient, fake_ai, fake_voice_gateway):
    """Auto mode: the first turn's detected language is pinned so later
    turns transcribe in it without re-detecting."""
    conv_id = await _create_conversation(client)
    fake_voice_gateway["transcripts"] = [("first", "ja"), "second"]

    ws = FakeVoiceWebSocket([])
    session = voice.VoiceSession(ws, conv_id)  # type: ignore[arg-type]
    for _ in range(2):
        await session.handle_control(_ctrl("speech_start")["text"])
        session.feed_pcm(b"\x00" * 16000)
        await session.handle_control(_ctrl("speech_end")["text"])
        assert session._turn_task is not None
        await session._turn_task

    # Turn 1 auto-detected "ja"; turn 2 ran with it pinned.
    assert fake_voice_gateway["stt_languages"] == [None, "ja"]


async def test_voice_full_name_detection_pins_as_iso(
    client: AsyncClient, fake_ai, fake_voice_gateway
):
    """Regression: whisper-1's verbose_json names languages ("english")
    but the API wants ISO-639-1 — pinning the raw name made every later
    turn 400 ("Invalid language")."""
    conv_id = await _create_conversation(client)
    fake_voice_gateway["transcripts"] = [("first", "english"), "second"]

    ws = FakeVoiceWebSocket([])
    session = voice.VoiceSession(ws, conv_id)  # type: ignore[arg-type]
    for _ in range(2):
        await session.handle_control(_ctrl("speech_start")["text"])
        session.feed_pcm(b"\x00" * 16000)
        await session.handle_control(_ctrl("speech_end")["text"])
        assert session._turn_task is not None
        await session._turn_task

    # The name is pinned as its ISO code — turn 2 sends "en", not "english".
    assert fake_voice_gateway["stt_languages"] == [None, "en"]
