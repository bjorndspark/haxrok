"""Server-side echo cancellation (app/core/echo.py): the far-end buffer,
the TTS resampler, backend selection, and the voice-session wiring. DSP
assertions run only where the native bindings are present (the backend
image ships both)."""

import math
import struct

import pytest
from httpx import AsyncClient

from app.core import echo
from app.core.config import settings
from app.features.chat import service, voice
from app.features.chat.router import voice_ws
from tests.test_chat import FakeAIClient, _create_conversation
from tests.test_voice import FakeVoiceWebSocket, _ctrl, _pcm

FRAME_B = echo.FRAME_BYTES


def _s16(samples: list[int]) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def _tone(hz: float, n: int, amp: float = 0.3) -> bytes:
    return _s16(
        [int(amp * 32767 * math.sin(2 * math.pi * hz * i / echo.SAMPLE_RATE)) for i in range(n)]
    )


@pytest.fixture
def fake_ai(monkeypatch):
    client = FakeAIClient(["Hello", ", ", "world"])
    monkeypatch.setattr(service, "get_ai_client", lambda: client)
    return client


@pytest.fixture
def fake_synth(monkeypatch):
    """pcm TTS one chunk per clip, prefixed with the negotiated format —
    the same shape test_voice's fake gateway produces."""

    async def synth(text: str, fmt: str):
        yield fmt.encode() + b":" + text.encode()[:8]

    monkeypatch.setattr(voice.ai_media, "synthesize_speech_stream", synth)


# --- _FarBuffer -----------------------------------------------------------


def test_far_buffer_seeded_delay_then_fifo():
    buf = echo._FarBuffer(delay_samples=3)
    buf.feed(_s16([10, 20, 30, 40]))
    assert buf.take(4 * 2) == _s16([0, 0, 0, 10])  # seed drains first
    assert buf.take(3 * 2) == _s16([20, 30, 40])


def test_far_buffer_empty_take_is_silence():
    buf = echo._FarBuffer()
    assert buf.take(FRAME_B) == b"\x00" * FRAME_B


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_far_buffer_drains_by_wall_clock_without_mic():
    """Regression: the client sends no mic audio while a reply plays (VAD
    paused), so draining only on mic frames left the whole reply queued —
    the next utterance was "cancelled" against audio long finished. Wall
    time spent playing with no mic frames is skipped."""
    clock = _Clock()
    buf = echo._FarBuffer(clock=clock)
    second = echo.SAMPLE_RATE * 2
    buf.feed(b"\x01\x00" * echo.SAMPLE_RATE)  # 1s of reply, value 1
    buf.feed(b"\x02\x00" * echo.SAMPLE_RATE)  # then 1s of value 2
    clock.t += 1.5  # 1.5s of playback, no mic frames at all
    head = buf.take(4)
    # Positioned ~1.5s in: inside the second second, not at the start.
    assert head == b"\x02\x00\x02\x00"
    assert len(buf._q) < second


def test_far_buffer_tolerates_bursty_mic_delivery():
    """Continuous-but-clumped mic frames must not skip reference samples."""
    clock = _Clock()
    buf = echo._FarBuffer(clock=clock)
    samples = list(range(1, 4001))  # 250ms — more than the 10 frames taken
    buf.feed(_s16(samples))
    out = bytearray()
    for _ in range(5):
        clock.t += 0.04  # two 20ms frames' worth of time …
        out += buf.take(FRAME_B)  # … delivered back to back
        out += buf.take(FRAME_B)
    n = len(out) // 2
    assert struct.unpack(f"<{n}h", out) == tuple(samples[:n])


def test_far_buffer_restart_seeds_delay_again():
    clock = _Clock()
    buf = echo._FarBuffer(delay_samples=2, clock=clock)
    buf.feed(_s16([5]))
    assert buf.take(6) == _s16([0, 0, 5])
    clock.t += 10.0  # idle, queue empty
    buf.feed(_s16([7]))
    assert buf.take(6) == _s16([0, 0, 7])


def test_far_buffer_clear_drops_queued():
    buf = echo._FarBuffer()
    buf.feed(_s16([1] * 100))
    buf.clear()
    assert buf.take(4) == b"\x00" * 4


# --- resample ---------------------------------------------------------------


def test_resample_24k_to_16k():
    src = _tone(440, 2400)  # 100ms at 24kHz
    out = echo.resample_24k_to_16k(src)
    assert len(out) == 1600 * 2  # 100ms at 16kHz
    assert struct.unpack("<h", out[:2])[0] == struct.unpack("<h", src[:2])[0]
    # The interpolated tail lands within ~half a source-sample of the end.
    assert abs(struct.unpack("<h", out[-2:])[0] - struct.unpack("<h", src[-2:])[0]) <= 3000


def test_resample_short_input():
    assert echo.resample_24k_to_16k(b"\x00") == b""
    assert echo.resample_24k_to_16k(b"\x01\x00") == b""


# --- create() -----------------------------------------------------------------


def test_create_off_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "VOICE_ECHO_CANCELLER", "off")
    assert echo.create() is None


def test_create_speex_missing_lib_disables(monkeypatch):
    def _boom():
        raise OSError("libspeexdsp not found")

    monkeypatch.setattr(settings, "VOICE_ECHO_CANCELLER", "speex")
    monkeypatch.setattr(echo, "_load_speex", _boom)
    assert echo.create() is None


def _speex_lib():
    try:
        return echo._load_speex()
    except OSError:
        return None


def _webrtc_available() -> bool:
    try:
        import pywebrtc_audio  # noqa: F401
    except ImportError:
        return False
    return True


# --- session wiring --------------------------------------------------------


async def test_voice_session_routes_audio_through_canceller(
    client: AsyncClient, fake_ai, fake_synth, monkeypatch
):
    """feed_pcm passes through the canceller (the STT call sees processed
    audio), sent pcm chunks reach the far-end reference resampled to 16kHz,
    and the canceller is closed with the session."""
    calls: dict = {"render": [], "reset": 0, "closed": 0}
    seen: dict = {}

    class FakeEC:
        def feed_render(self, pcm):
            calls["render"].append(pcm)

        def process(self, pcm):
            return b"\x01" * len(pcm)  # visibly transform the mic audio

        def reset_far(self):
            calls["reset"] += 1

        def close(self):
            calls["closed"] += 1

    async def fake_transcribe(data: bytes, filename: str, language=None):
        seen["pcm"] = data[44:]  # strip the WAV header
        return "hi", "en"

    monkeypatch.setattr(voice.echo, "create", lambda: FakeEC())
    monkeypatch.setattr(voice.ai_media, "transcribe_audio_verbose", fake_transcribe)
    monkeypatch.setattr(voice, "MIN_STT_BYTES", 1)

    conv_id = await _create_conversation(client)
    ws = FakeVoiceWebSocket([_ctrl("speech_start"), _pcm(16000), _ctrl("speech_end")])
    await voice_ws(ws, conv_id)

    assert seen["pcm"] == b"\x01" * 16000
    # The fake gateway's one pcm clip — 24kHz samples resampled to 16kHz.
    assert calls["render"] == [echo.resample_24k_to_16k(b"pcm:Hello, w")]
    assert calls["closed"] == 1


async def test_voice_mp3_clips_feed_no_far(client: AsyncClient, fake_ai, monkeypatch):
    """mp3 audio can't be a reference — nothing reaches feed_render."""
    calls: list = []

    class FakeEC:
        def feed_render(self, pcm):
            calls.append(pcm)

        def process(self, pcm):
            return pcm

        def reset_far(self):
            pass

        def close(self):
            pass

    async def fake_transcribe(data: bytes, filename: str, language=None):
        return "hi", "en"

    async def fake_synth(text: str, fmt: str):
        if fmt == "pcm":
            from app.core.errors import UpstreamError

            raise UpstreamError("unsupported format")
        yield b"MP3DATA"

    monkeypatch.setattr(voice.echo, "create", lambda: FakeEC())
    monkeypatch.setattr(voice.ai_media, "transcribe_audio_verbose", fake_transcribe)
    monkeypatch.setattr(voice.ai_media, "synthesize_speech_stream", fake_synth)
    monkeypatch.setattr(voice, "MIN_STT_BYTES", 1)

    conv_id = await _create_conversation(client)
    ws = FakeVoiceWebSocket([_ctrl("speech_start"), _pcm(16000), _ctrl("speech_end")])
    await voice_ws(ws, conv_id)

    assert calls == []


async def test_voice_interrupt_resets_far(client: AsyncClient, monkeypatch):
    calls: dict = {"reset": 0, "closed": 0}

    class FakeEC:
        def feed_render(self, pcm):
            pass

        def process(self, pcm):
            return pcm

        def reset_far(self):
            calls["reset"] += 1

        def close(self):
            calls["closed"] += 1

    monkeypatch.setattr(voice.echo, "create", lambda: FakeEC())
    conv_id = await _create_conversation(client)
    ws = FakeVoiceWebSocket([_ctrl("interrupt")])
    await voice_ws(ws, conv_id)

    assert calls["reset"] == 1
    assert calls["closed"] == 1


# --- real DSP (skips where the binding isn't installed) --------------------


def _erle_db(echo_in: bytes, out: bytes) -> float:
    """Echo return loss enhancement: how much quieter `out` is than the
    echo signal at the same positions."""
    n = min(len(echo_in), len(out)) // 2
    a = struct.unpack(f"<{n}h", echo_in[: n * 2])
    b = struct.unpack(f"<{n}h", out[: n * 2])
    ra = math.sqrt(sum(x * x for x in a) / n)
    rb = math.sqrt(sum(x * x for x in b) / n)
    return 20 * math.log10(ra / max(rb, 1e-9))


def _echo_case(seconds: float = 6.0, delay_ms: int = 60):
    """Synthetic near/far: far = modulated sine bursts; near = a delayed,
    attenuated copy (the echo, nothing else — ERLE scores suppression)."""
    n = int(seconds * echo.SAMPLE_RATE)
    delay = delay_ms * echo.SAMPLE_RATE // 1000
    far = bytearray()
    for i in range(n):
        env = 1.0 if math.sin(2 * math.pi * 3 * i / echo.SAMPLE_RATE) > 0 else 0.0
        far += struct.pack("<h", int(0.4 * env * 32767 * math.sin(i * 0.37)))
    near = bytearray(delay * 2)  # echo arrives `delay` samples late
    for i in range(n - delay):
        v = struct.unpack_from("<h", far, i * 2)[0]
        near += struct.pack("<h", int(0.6 * v))
    return bytes(far), bytes(near)


@pytest.mark.skipif(_speex_lib() is None, reason="libspeexdsp not installed")
def test_speex_cancels_delayed_echo():
    ec = echo._SpeexEcho(_speex_lib(), delay_ms=0, filter_ms=256)
    far, near = _echo_case()
    ec.feed_render(far)
    out = bytearray()
    for off in range(0, len(near), 1024):
        out += ec.process(near[off : off + 1024])
    ec.close()
    tail = len(out) // 2  # score after the filter has converged
    assert _erle_db(near[tail:], bytes(out[tail:])) > 3


@pytest.mark.skipif(not _webrtc_available(), reason="pywebrtc-audio not installed")
def test_webrtc_cancels_delayed_echo():
    ec = echo._WebRTCEcho(delay_ms=0)
    far, near = _echo_case()
    ec.feed_render(far)
    out = bytearray()
    for off in range(0, len(near), 1024):
        out += ec.process(near[off : off + 1024])
    tail = len(out) // 2
    assert _erle_db(near[tail:], bytes(out[tail:])) > 3
