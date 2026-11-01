"""Voice-mode session: one WebSocket carries mic audio upstream and the
whole turn downstream — a single-shot (often speculative) transcript, the
streamed reply, and punctuation-chunked TTS audio.

Wire protocol — client → server:
  binary frame                  PCM s16le 16kHz mono mic audio (while speaking)
  {"type": "speech_start"}      client VAD: utterance begins; optional
                                "language" ISO code pins the recognizer
                                (absent = auto-detect)
  {"type": "speech_maybe_end"}  ~0.5s of low speech-prob frames — the utterance
                                is probably over: snapshot the buffered audio
                                and run the turn speculatively
  {"type": "speech_continue"}   speech resumed before the VAD redemption fired —
                                discard the speculative turn
  {"type": "speech_end"}        client VAD confirmed end-of-speech → release the
                                speculative turn, or run one now
  {"type": "speech_cancel"}     VAD misfire (too short to be speech) — drop the
                                utterance and any speculation on it
  {"type": "interrupt"}         cancel the in-flight reply (barge-in)

Server → client — JSON frames share the {event, data: <json string>}
envelope of chat_ws; binary frames carry TTS audio between markers:
  transcript         {text, language}     final transcript of the utterance
  token | message | error                 identical to stream_reply events
  audio_begin {seq, format, sample_rate}  → binary chunks → audio_end {seq}
  interrupted        {}                   reply cancelled; drop queued audio
  turn_end           {}                   turn fully delivered; resume VAD

Latency shape: the whole utterance is transcribed in one shot — one STT
call per utterance, no window stitching, no seam artifacts. The call fires
on the client's *probable* end-of-speech signal (~0.5s of quiet), well
before its VAD redemption (1.2s) confirms it, so STT + LLM TTFB + first
TTS bytes overlap the client's own trailing-silence hold.

Speculation is invisible to the client: a speculative turn's frames are
held (``_TurnOutput``) and nothing is persisted until ``speech_end``
confirms the utterance. A misfired speculation (speech resumes, or the VAD
calls a misfire) costs the wasted upstream calls and nothing else — no
rows, no frames, no split utterance.
"""

import asyncio
import json
import re
import struct
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Literal

from openai.types.chat import ChatCompletionMessageParam
from starlette.websockets import WebSocket

from app.core import echo
from app.core.ai import media as ai_media
from app.core.errors import NotFoundError, UpstreamError
from app.core.logging import get_logger
from app.features.chat import service
from app.features.chat.models import Message
from app.features.chat.schemas import MessageRead

logger = get_logger(__name__)

# Mic audio arrives as PCM s16le 16kHz mono — vad-web's native format.
SAMPLE_RATE = 16000
_BYTES_PER_SEC = SAMPLE_RATE * 2

# The utterance buffer holds one whole utterance for the single-shot STT
# call — capped at whisper's ~30s context so a rambling turn can't grow
# memory without bound.
RING_BUFFER_S = 30.0
MIN_STT_BYTES = int(0.4 * _BYTES_PER_SEC)  # shorter clips transcribe to junk
# A hung upstream call must not wedge the session: a dead transcription
# drops the turn (client resumes listening), a dead clip drops from playback.
STT_TIMEOUT_S = 20.0  # whole-utterance transcriptions scale with length

# Reply TTS: cut at sentence punctuation always, at clause punctuation once
# the fragment is long enough to speak naturally; hard-cut run-ons.
CLAUSE_MIN_CHARS = 12
HARD_CUT_CHARS = 80
TTS_MAX_IN_FLIGHT = 2
TTS_PCM_SAMPLE_RATE = 24000  # OpenAI-compatible "pcm" format: s16le mono
TTS_TIMEOUT_S = 10.0  # per format attempt, first-byte-to-last

_WS_CLOSE_NOT_FOUND = 4404  # matches chat_ws

# whisper-1's verbose_json answers with a full name ("english") where the
# transcriptions API wants ISO-639-1 back — pinning it as-is makes every
# later turn 400 ("Invalid language"). Map the names we know and pin only
# ISO-shaped values; the rest re-detect per turn, which is free.
_LANG_NAME_TO_ISO = {"english": "en", "japanese": "ja"}
_ISO639_1 = re.compile(r"[a-z]{2,3}")


def wav_wrap(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw s16le mono PCM in a WAV header — whisper wants a file."""
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )


# Sentence-final marks always cut; clause marks cut once the fragment has
# enough chars to synthesize naturally.
_BOUNDARY = re.compile(r"[.!?。！？…\n,，、;；:：]")
_SENTENCE_END = set(".!?。！？…\n")


class SpeechChunker:
    """Incremental punctuation splitter: feed streamed reply tokens in,
    get speakable fragments out — the first TTS call fires one clause in
    rather than at the end of the reply."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, token: str) -> list[str]:
        self._buf += token
        return self._drain()

    def flush(self) -> str | None:
        tail = self._buf.strip()
        self._buf = ""
        return tail if tail and any(c.isalnum() for c in tail) else None

    def _drain(self) -> list[str]:
        out: list[str] = []
        while True:
            cut_end = -1
            for m in _BOUNDARY.finditer(self._buf):
                frag = self._buf[: m.end()]
                if not any(c.isalnum() for c in frag):
                    continue  # bare punctuation — nothing to speak yet
                # Cut at the first qualifying boundary: sentence-end always
                # qualifies; a clause mark qualifies once the accumulated
                # fragment is long enough to speak naturally. A short clause
                # fragment ("Yes,") merges forward into whatever follows.
                if m.group() in _SENTENCE_END or len(frag.strip()) >= CLAUSE_MIN_CHARS:
                    cut_end = m.end()
                    break
            if cut_end < 0:
                break
            out.append(self._buf[:cut_end].strip())
            self._buf = self._buf[cut_end:]
        if len(self._buf) >= HARD_CUT_CHARS:
            out.append(self._buf[:HARD_CUT_CHARS])
            self._buf = self._buf[HARD_CUT_CHARS:]
        return out


class _TurnOutput:
    """One turn's outbound frames, gated on the utterance being confirmed.

    A speculative turn runs ahead of the client's VAD, but the client must
    never see a reply to speech that wasn't over: until ``confirmed`` is
    set, frames are held in production order; then a release task flushes
    them and later frames go straight out. A speculation cancelled before
    confirmation is dropped unseen. A turn started on ``speech_end`` is
    confirmed from the start and never holds anything.
    """

    def __init__(self, session: "VoiceSession", confirmed: asyncio.Event) -> None:
        self._s = session
        # Held sends, in production order, as deferred calls.
        self._held: list[Callable[[], Awaitable[None]]] | None = None if confirmed.is_set() else []
        self._release = (
            None if self._held is None else asyncio.create_task(self._release_on(confirmed))
        )

    async def event(self, name: str, data: dict) -> None:
        if self._held is not None:
            self._held.append(lambda: self._s.send_event(name, data))
        else:
            await self._s.send_event(name, data)

    async def audio(self, chunk: bytes, *, render: bool) -> None:
        """A TTS chunk; ``render`` marks pcm the echo canceller may use as
        its far-end reference (fed at actual send time, not production)."""
        if self._held is not None:
            self._held.append(lambda: self._s.send_audio(chunk, render=render))
        else:
            await self._s.send_audio(chunk, render=render)

    async def opened(self) -> None:
        """Wait until the turn is confirmed and its held frames are out."""
        if self._release is not None:
            await self._release

    def abort(self) -> None:
        if self._release is not None:
            self._release.cancel()

    async def _release_on(self, confirmed: asyncio.Event) -> None:
        await confirmed.wait()
        held = self._held
        assert held is not None
        # Frames produced while flushing append to the same list and go out
        # in order; the gate opens only once nothing is left (no await
        # between the empty check and the switch).
        while held:
            await held.pop(0)()
        self._held = None


class _TtsPipeline:
    """Synthesizes reply fragments concurrently (≤TTS_MAX_IN_FLIGHT) but
    emits clips strictly in order: audio_begin → binary chunks → audio_end.

    Each clip's queue carries its format first, then audio bytes, then None
    (sentinel). No lock spans a clip: frames are sent one at a time, so
    token frames for later text can interleave with a clip's chunks — the
    client tells them apart by WS frame type.
    """

    def __init__(self, session: "VoiceSession", out: _TurnOutput) -> None:
        self._s = session
        self._out = out
        self._queues: dict[int, asyncio.Queue[str | bytes | None]] = {}
        self._next = 0
        self._closed = False
        self._sem = asyncio.Semaphore(TTS_MAX_IN_FLIGHT)
        self._new_clip = asyncio.Event()
        self._synth_tasks: set[asyncio.Task[None]] = set()
        self._emit_task = asyncio.create_task(self._emit_loop())

    def add(self, text: str) -> None:
        seq = self._next
        self._next += 1
        q: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        self._queues[seq] = q
        self._new_clip.set()
        task = asyncio.create_task(self._synth(text, q))
        self._synth_tasks.add(task)
        task.add_done_callback(self._synth_tasks.discard)

    def close(self) -> None:
        self._closed = True
        self._new_clip.set()

    async def wait(self) -> None:
        await self._emit_task

    def abort(self) -> None:
        for t in self._synth_tasks:
            t.cancel()
        self._emit_task.cancel()

    async def _synth(self, text: str, q: asyncio.Queue[str | bytes | None]) -> None:
        """Fill the clip queue: format marker, audio chunks, sentinel.
        pcm streams raw samples (best latency); if the gateway rejects it,
        this clip and all later ones fall back to mp3."""
        formats: list[Literal["pcm", "mp3"]] = ["pcm", "mp3"] if self._s.pcm_ok else ["mp3"]
        try:
            async with self._sem:
                for fmt in formats:
                    emitted = False  # marker sent — clip's format is committed
                    try:
                        # aclosing: a timeout or cancel must close the
                        # upstream HTTP response now, not at GC time.
                        async with (
                            asyncio.timeout(TTS_TIMEOUT_S),
                            aclosing(ai_media.synthesize_speech_stream(text, fmt)) as stream,
                        ):
                            # Pull the first chunk before marking — a gateway
                            # that can't do pcm fails here, leaving the clip
                            # free to retry as mp3.
                            first = await anext(stream)
                            await q.put(fmt)
                            emitted = True
                            await q.put(first)
                            async for chunk in stream:
                                await q.put(chunk)
                    except StopAsyncIteration:
                        if not emitted:
                            await q.put(fmt)
                    except (UpstreamError, TimeoutError) as e:
                        detail = e.detail if isinstance(e, UpstreamError) else "timed out"
                        logger.warning(
                            "voice TTS (%s) failed%s: %s",
                            fmt,
                            " mid-stream" if emitted else "",
                            detail,
                        )
                        if not emitted:
                            if fmt == "pcm":
                                self._s.pcm_ok = False
                            continue  # retry the next format
                    break
        finally:
            # The sentinel must land even on cancel/crash — the emitter
            # waits on it to close the clip.
            await q.put(None)

    async def _emit_loop(self) -> None:
        seq = 0
        while True:
            while seq not in self._queues:
                if self._closed and seq >= self._next:
                    return
                self._new_clip.clear()
                await self._new_clip.wait()
            q = self._queues.pop(seq)
            fmt = await q.get()
            if fmt is not None:
                meta: dict = {"seq": seq, "format": fmt}
                if fmt == "pcm":
                    meta["sample_rate"] = TTS_PCM_SAMPLE_RATE
                await self._out.event("audio_begin", meta)
                while isinstance(chunk := await q.get(), bytes):
                    # Only pcm can be the canceller's far-end reference —
                    # mp3 is encoded.
                    await self._out.audio(chunk, render=fmt == "pcm")
                await self._out.event("audio_end", {"seq": seq})
            seq += 1


@dataclass
class _Turn:
    """What a turn has produced so far — read by the cancel path to decide
    what (if anything) to persist."""

    confirmed: asyncio.Event
    transcript: str = ""
    parts: list[str] = field(default_factory=list)
    pipe: _TtsPipeline | None = None
    commit: asyncio.Task[Message | None] | None = None


class VoiceSession:
    """One voice-mode WebSocket connection. The router's receive loop calls
    feed_pcm/handle_control; the reply turn runs as a task so a slow gateway
    call never blocks frame handling.

    ``_pcm`` holds the current utterance: cleared at speech_start /
    speech_end / speech_cancel, kept across speech_continue so the next
    snapshot covers the whole utterance, pause included.
    """

    def __init__(self, websocket: WebSocket, conversation_id: int) -> None:
        self.websocket = websocket
        self.conversation_id = conversation_id
        self.send_lock = asyncio.Lock()
        self.pcm_ok = True  # flips False if the gateway rejects pcm TTS
        self._pcm = bytearray()
        self._speech_active = False
        self._turn_task: asyncio.Task[None] | None = None
        self._turn: _Turn | None = None
        self._language: str | None = None  # user's selector pick; None = auto
        self._detected_language: str | None = None  # whisper's detection, pinned
        self._echo = echo.create()  # server-side AEC; None unless enabled

    # --- inbound -------------------------------------------------------

    def feed_pcm(self, data: bytes) -> None:
        if self._echo is not None:
            data = self._echo.process(data)
        self._pcm += data
        overflow = len(self._pcm) - int(RING_BUFFER_S * _BYTES_PER_SEC)
        if overflow > 0:
            del self._pcm[:overflow]

    def feed_render(self, tts_pcm: bytes) -> None:
        """Queue a sent pcm TTS chunk as the canceller's far-end
        reference — resampled to the mic rate. No-op when server-side
        AEC is off."""
        if self._echo is not None:
            self._echo.feed_render(echo.resample_24k_to_16k(tts_pcm))

    def _turn_running(self) -> bool:
        return self._turn_task is not None and not self._turn_task.done()

    async def handle_control(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            msg = None
        if not isinstance(msg, dict):
            await self.send_event("error", {"message": "invalid control frame"})
            return
        match msg.get("type"):
            case "speech_start":
                # New utterance: a reply still in flight is a barge-in.
                if self._turn_running():
                    await self.interrupt()
                self._turn_task = None
                self._pcm.clear()
                self._speech_active = True
                if "language" in msg:
                    language = msg["language"] or None
                    if language != self._language:
                        # A changed selection drops the pinned detection —
                        # switching back to auto re-detects on the next turn.
                        self._language = language
                        self._detected_language = None
            case "speech_continue":
                # A pause, not an end: the speculation is invalid, but the
                # utterance (and its audio) continues.
                await self._discard_speculation()
            case "speech_maybe_end":
                # Probable end-of-speech: run the turn speculatively on a
                # snapshot. speech_end releases it; speech_continue or
                # speech_cancel discards it unseen.
                if (
                    self._speech_active
                    and not self._turn_running()
                    and len(self._pcm) >= MIN_STT_BYTES
                ):
                    self._start_turn(bytes(self._pcm), confirmed=False)
            case "speech_end":
                await self._speech_end()
            case "speech_cancel":
                # VAD misfire: the "utterance" was a blip — drop it whole.
                self._speech_active = False
                self._pcm.clear()
                await self._discard_speculation()
            case "interrupt":
                await self.interrupt()
            case other:
                await self.send_event("error", {"message": f"unknown control: {other}"})

    async def aclose(self) -> None:
        task = self._turn_task
        if task is not None and not task.done():
            task.cancel()
            # Let the cancel path finish persisting a confirmed turn before
            # the connection's resources go away.
            await asyncio.gather(task, return_exceptions=True)
        if self._echo is not None:
            self._echo.close()

    # --- reply turn ----------------------------------------------------

    def _start_turn(self, pcm: bytes, *, confirmed: bool) -> None:
        turn = _Turn(confirmed=asyncio.Event())
        if confirmed:
            turn.confirmed.set()
        self._turn = turn
        self._turn_task = asyncio.create_task(self._run_turn(pcm, turn))
        self._turn_task.add_done_callback(self._on_turn_done)

    async def _speech_end(self) -> None:
        self._speech_active = False
        pcm = bytes(self._pcm)
        self._pcm.clear()
        if self._turn_running():
            # The speculation was right — release it. Audio after its
            # snapshot is the VAD's trailing silence.
            assert self._turn is not None
            self._turn.confirmed.set()
            return
        if self._turn_task is not None:
            # A turn already resolved without waiting for us (e.g. it hit a
            # missing conversation); the client still owes itself a turn_end.
            await self.send_event("turn_end", {})
            return
        if len(pcm) < MIN_STT_BYTES:
            await self.send_event("transcript", {"text": ""})
            await self.send_event("turn_end", {})
            return
        self._start_turn(pcm, confirmed=True)

    async def _discard_speculation(self) -> None:
        """Cancel an unconfirmed turn quietly — the client never saw it."""
        task, turn = self._turn_task, self._turn
        self._turn_task = None
        if task is None or task.done():
            return
        if turn is not None and turn.confirmed.is_set():
            # Already released — the client saw it, so it needs the signal.
            self._turn_task = task
            await self.interrupt()
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _run_turn(self, pcm: bytes, turn: _Turn) -> None:
        out = _TurnOutput(self, turn.confirmed)
        try:
            try:
                await self._produce(pcm, turn, out)
            except NotFoundError as e:
                if turn.pipe is not None:
                    turn.pipe.abort()
                await out.event("error", {"message": e.detail})
                await out.opened()
                await self.websocket.close(code=_WS_CLOSE_NOT_FOUND)
                return
            except Exception:
                # Any silent failure leaves the client stuck in "thinking" —
                # always tell it the turn died.
                if turn.pipe is not None:
                    turn.pipe.abort()
                logger.exception("voice turn failed")
                await out.event("error", {"message": "voice turn failed"})
            await out.opened()
            await out.event("turn_end", {})
        except asyncio.CancelledError:
            if turn.pipe is not None:
                turn.pipe.abort()
            out.abort()
            await self._salvage(turn)
            raise

    async def _produce(self, pcm: bytes, turn: _Turn, out: _TurnOutput) -> None:
        """Transcribe, stream the reply + TTS, and persist once confirmed.
        Returns normally on a dead/empty transcription (the caller sends
        turn_end); raises on anything else."""
        try:
            async with asyncio.timeout(STT_TIMEOUT_S):
                text, detected = await ai_media.transcribe_audio_verbose(
                    wav_wrap(pcm),
                    "speech.wav",
                    language=self._language or self._detected_language,
                )
        except (UpstreamError, TimeoutError) as e:
            detail = e.detail if isinstance(e, UpstreamError) else "timed out"
            logger.warning("voice STT failed: %s", detail)
            await out.event("error", {"message": "transcription failed"})
            return
        # Auto mode pins the detection for the session when it's a value
        # the API accepts back — stable across turns, and the client can
        # show "Auto (日本語)". Full-name detections re-detect per turn.
        detected_iso = _LANG_NAME_TO_ISO.get(detected or "", detected)
        if self._language is None and detected_iso and _ISO639_1.fullmatch(detected_iso):
            self._detected_language = detected_iso
        turn.transcript = text.strip()
        await out.event(
            "transcript",
            {"text": turn.transcript, "language": self._language or detected_iso or detected},
        )
        if not turn.transcript:
            return

        # The user message isn't persisted yet (it waits for confirmation),
        # so the reply's prompt is the stored history plus this transcript.
        user: ChatCompletionMessageParam = {"role": "user", "content": turn.transcript}
        messages = [*await service.load_history(self.conversation_id), user]
        chunker = SpeechChunker()
        turn.pipe = pipe = _TtsPipeline(self, out)
        failed = False
        try:
            async with aclosing(service.stream_tokens(messages)) as tokens:
                async for token in tokens:
                    turn.parts.append(token)
                    for frag in chunker.feed(token):
                        pipe.add(frag)
                    await out.event("token", {"token": token})
        except UpstreamError as e:
            failed = True
            await out.event("error", {"message": e.detail})
        if (tail := chunker.flush()) is not None:
            pipe.add(tail)
        pipe.close()

        # Nothing persists until the utterance is confirmed — a speculation
        # cancelled before this point leaves no rows behind.
        await out.opened()
        reply = None if failed and not turn.parts else "".join(turn.parts)
        turn.commit = asyncio.ensure_future(
            service.record_exchange(self.conversation_id, turn.transcript, reply)
        )
        # Shielded: a barge-in landing mid-commit must not leave it
        # half-done; the cancel path waits for it instead.
        msg = await asyncio.shield(turn.commit)
        if msg is not None:
            await out.event("message", MessageRead.model_validate(msg).model_dump(mode="json"))
        await pipe.wait()

    async def _salvage(self, turn: _Turn) -> None:
        """Cancel path: a confirmed turn cut short (barge-in, Stop,
        disconnect) keeps what was said and the reply so far. Runs inside
        the cancelled task, so ``interrupted`` can't outrun the commit —
        the client refetches on it."""
        try:
            if turn.commit is not None:
                await asyncio.gather(turn.commit, return_exceptions=True)
            elif turn.transcript and turn.confirmed.is_set():
                await service.record_exchange(
                    self.conversation_id, turn.transcript, "".join(turn.parts) or None
                )
        except Exception:
            logger.exception("voice turn: failed to save the interrupted turn")

    async def interrupt(self) -> None:
        task = self._turn_task
        self._turn_task = None
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._echo is not None:
            # The client dropped queued audio — samples still in the far
            # buffer will never play, so they can't echo either.
            self._echo.reset_far()
        await self.send_event("interrupted", {})

    def _on_turn_done(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (e := task.exception()):
            logger.error("voice turn crashed", exc_info=e)

    # --- outbound ------------------------------------------------------

    async def send_event(self, event: str, data: dict) -> None:
        """Same {event, data: <json string>} envelope as chat_ws."""
        async with self.send_lock:
            await self.websocket.send_json({"event": event, "data": json.dumps(data)})

    async def send_audio(self, chunk: bytes, *, render: bool) -> None:
        """One binary TTS frame; pcm frames also become the echo
        canceller's far-end reference as they leave."""
        async with self.send_lock:
            await self.websocket.send_bytes(chunk)
        if render:
            self.feed_render(chunk)
