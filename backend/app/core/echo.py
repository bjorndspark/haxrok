"""Server-side echo cancellation for voice mode.

The browser's getUserMedia AEC is the primary canceller — it sees the
exact render signal with exact timing. This module is a second line for
clients/platforms where that leaks (echo triggers phantom barge-ins or
pollutes the STT utterance). Two backends, selected by
``VOICE_ECHO_CANCELLER``:

- ``"speex"``  — SpeexDSP's canceller via ctypes against libspeexdsp1
                 (a runtime .so installed in the backend image; no
                 Python build step).
- ``"webrtc"`` — WebRTC AEC3 via the pywebrtc-audio wheel (numpy) — the
                 optional ``webrtc`` extra.

Both share one alignment model, in ``_FarBuffer``: TTS samples the
server sent are queued, and the queue head plays out in realtime. Each
mic frame consumes the reference for its own span; when mic audio stops
arriving (the client only streams while the user speaks, and the VAD
pauses during playback), the wall clock keeps draining the queue, so the
next mic frame lines up with what is audible *now*, not with a reply
that finished long ago. Because the client plays clips in order at
realtime, the queue depth approximates its playback buffer — a clip
synthesized faster than realtime stays queued roughly until it's
audible. What remains unmodelled (network transit, the acoustic path)
is left to each canceller's adaptive filter / delay estimator;
``VOICE_EC_DELAY_MS`` adds a fixed lag at the start of each playback
burst for high-latency paths.

Alignment is approximate by nature — the server knows what it *sent*,
not what played. If the estimate is wrong, under-estimating the delay
is safer than over-estimating it: cancellers only look back at far-end
history, never forward.
"""

import ctypes
import ctypes.util
import struct
import time
from collections.abc import Callable
from typing import Protocol

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Mic/utterance format: s16le mono at the VAD's native rate.
SAMPLE_RATE = 16000
# TTS "pcm" clips arrive at the OpenAI-compatible rate — the far-end
# reference must be resampled before it can cancel 16kHz mic audio.
TTS_SAMPLE_RATE = 24000
# Canceller frame size. Speex needs fixed frames; feeding the same size
# to AEC3 keeps both backends symmetric and bounds processing latency.
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2


def resample_24k_to_16k(pcm: bytes) -> bytes:
    """s16le mono 24kHz → 16kHz, linear interpolation (3:2 downsample)."""
    n = len(pcm) // 2
    if n < 2:
        return b""
    src = struct.unpack(f"<{n}h", pcm[: n * 2])
    out_len = n * 2 // 3
    out = bytearray(out_len * 2)
    for i in range(out_len):
        pos = i * 1.5
        j = int(pos)
        frac = pos - j
        v = src[j] + (src[j + 1] - src[j]) * frac if j + 1 < n else src[j]
        struct.pack_into("<h", out, i * 2, int(v))
    return bytes(out)


class _FarBuffer:
    """Ordered queue of sent render samples whose head plays out in
    realtime.

    Mic frames consume the reference for their own span (``take``). The
    wall clock is the backstop: time that passes with the queue non-empty
    but no mic audio arriving is playback the server never saw a mic frame
    for, so once that unaccounted time exceeds ``_JITTER_S`` it is dropped
    from the head. The jitter allowance keeps bursty-but-continuous mic
    delivery (frames arriving in clumps) from skipping reference samples.
    ``delay_samples`` of silence lead every playback burst, modelling
    fixed network/acoustic lag the canceller's filter can't reach.
    """

    _JITTER_S = 0.25
    # Never hold more than this much reference — a backstop on memory, far
    # past any realistic playback buffer.
    _MAX_S = 60.0

    def __init__(self, delay_samples: int = 0, clock: Callable[[], float] = time.monotonic) -> None:
        self._delay = bytes(delay_samples * 2)
        self._q = bytearray()
        self._clock = clock
        self._last = clock()
        self._debt = 0  # bytes played (wall clock) minus bytes taken (mic)

    def feed(self, pcm: bytes) -> None:
        if not self._q:
            # Playback (re)starts now: seed the fixed lag, restart the clock.
            self._q += self._delay
            self._last = self._clock()
            self._debt = 0
        else:
            self._advance(0)
        self._q += pcm
        overflow = len(self._q) - int(self._MAX_S * SAMPLE_RATE) * 2
        if overflow > 0:
            del self._q[:overflow]

    def take(self, nbytes: int) -> bytes:
        """Pop the next ``nbytes`` of reference; silence when the queue
        ran dry (nothing playing, or the client fell behind sends)."""
        self._advance(nbytes)
        head = bytes(self._q[:nbytes])
        del self._q[:nbytes]
        if len(head) < nbytes:
            head += b"\x00" * (nbytes - len(head))
        return head

    def clear(self) -> None:
        """Playback was cut (interrupt) — queued samples will never
        reach the speaker, so they must never enter the filter."""
        self._q.clear()
        self._debt = 0

    def _advance(self, consumed: int) -> None:
        now = self._clock()
        elapsed = int((now - self._last) * SAMPLE_RATE) * 2
        self._last = now
        jitter = int(self._JITTER_S * SAMPLE_RATE) * 2
        self._debt = max(self._debt + elapsed - consumed, -jitter)
        if self._debt > jitter:
            # Played while no mic audio arrived — skip it (bytes stay even).
            drop = min(self._debt, len(self._q)) & ~1
            del self._q[:drop]
            self._debt = 0


class EchoCanceller(Protocol):
    """Per-session canceller: render audio in, mic audio through."""

    def feed_render(self, pcm16k: bytes) -> None: ...
    def process(self, pcm16k: bytes) -> bytes: ...
    def reset_far(self) -> None: ...
    def close(self) -> None: ...


# SpeexDSP echo API — ctypes over libspeexdsp.so.1 (apt: libspeexdsp1).
_SPEEX_ECHO_SET_SAMPLING_RATE = 24


def _load_speex() -> ctypes.CDLL:
    path = ctypes.util.find_library("speexdsp")
    if not path:
        raise OSError("libspeexdsp not found (install libspeexdsp1)")
    lib = ctypes.CDLL(path)
    lib.speex_echo_state_init.restype = ctypes.c_void_p
    lib.speex_echo_state_init.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.speex_echo_state_destroy.argtypes = [ctypes.c_void_p]
    lib.speex_echo_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    i16p = ctypes.POINTER(ctypes.c_int16)
    lib.speex_echo_playback.argtypes = [ctypes.c_void_p, i16p]
    lib.speex_echo_capture.argtypes = [ctypes.c_void_p, i16p, i16p]
    return lib


def _i16ptr(buf: bytes) -> ctypes.Array[ctypes.c_int16]:
    return (ctypes.c_int16 * (len(buf) // 2)).from_buffer_copy(buf)


class _SpeexEcho:
    """SpeexDSP MDF canceller. The filter tail (``filter_ms``) must cover
    the residual delay between the queued reference and the echo in the
    mic — over it, echo passes through."""

    def __init__(self, lib: ctypes.CDLL, delay_ms: int, filter_ms: int) -> None:
        self._lib = lib
        self._far = _FarBuffer(delay_ms * SAMPLE_RATE // 1000)
        self._pending = bytearray()
        self._st = lib.speex_echo_state_init(FRAME_SAMPLES, filter_ms * SAMPLE_RATE // 1000)
        rate = ctypes.c_int(SAMPLE_RATE)
        lib.speex_echo_ctl(self._st, _SPEEX_ECHO_SET_SAMPLING_RATE, ctypes.byref(rate))

    def feed_render(self, pcm16k: bytes) -> None:
        self._far.feed(pcm16k)

    def reset_far(self) -> None:
        self._far.clear()

    def process(self, pcm16k: bytes) -> bytes:
        self._pending += pcm16k
        out = bytearray()
        while len(self._pending) >= FRAME_BYTES:
            near = bytes(self._pending[:FRAME_BYTES])
            del self._pending[:FRAME_BYTES]
            far = self._far.take(FRAME_BYTES)
            self._lib.speex_echo_playback(self._st, _i16ptr(far))
            buf = (ctypes.c_int16 * FRAME_SAMPLES)()
            self._lib.speex_echo_capture(self._st, _i16ptr(near), buf)
            out += bytes(buf)
        return bytes(out)

    def close(self) -> None:
        if self._st:
            self._lib.speex_echo_state_destroy(self._st)
            self._st = None


class _WebRTCEcho:
    """WebRTC AEC3 via pywebrtc-audio. Its internal delay estimator
    handles residual far/near skew on its own — the far queue still
    matters: it paces render samples at playout rate, which is what the
    estimator assumes."""

    def __init__(self, delay_ms: int) -> None:
        import numpy as np
        from pywebrtc_audio import AudioProcessor

        self._np = np
        self._ap = AudioProcessor(sample_rate=SAMPLE_RATE, echo_cancellation=True)
        self._far = _FarBuffer(delay_ms * SAMPLE_RATE // 1000)
        self._pending = bytearray()

    def feed_render(self, pcm16k: bytes) -> None:
        self._far.feed(pcm16k)

    def reset_far(self) -> None:
        self._far.clear()

    def process(self, pcm16k: bytes) -> bytes:
        np = self._np
        self._pending += pcm16k
        out = bytearray()
        while len(self._pending) >= FRAME_BYTES:
            near = bytes(self._pending[:FRAME_BYTES])
            del self._pending[:FRAME_BYTES]
            far = np.frombuffer(self._far.take(FRAME_BYTES), dtype=np.int16)
            cleaned = self._ap.process(np.frombuffer(near, dtype=np.int16), far)
            out += cleaned.astype(np.int16).tobytes()
        return bytes(out)

    def close(self) -> None:
        pass  # AEC3 state dies with the object — nothing to release.


def create() -> EchoCanceller | None:
    """Build a per-session canceller per VOICE_ECHO_CANCELLER; ``None``
    when off or when the backend's binding is missing (voice still works
    — the browser's own AEC stays the only line)."""
    s = settings
    match s.VOICE_ECHO_CANCELLER:
        case "off":
            return None
        case "speex":
            try:
                lib = _load_speex()
            except OSError as e:
                logger.warning("voice echo canceller disabled: %s", e)
                return None
            logger.info(
                "voice echo canceller: speex (delay=%dms filter=%dms)",
                s.VOICE_EC_DELAY_MS,
                s.VOICE_EC_FILTER_MS,
            )
            return _SpeexEcho(lib, s.VOICE_EC_DELAY_MS, s.VOICE_EC_FILTER_MS)
        case "webrtc":
            try:
                ec = _WebRTCEcho(s.VOICE_EC_DELAY_MS)
            except ImportError:
                logger.warning("voice echo canceller disabled: pywebrtc-audio not installed")
                return None
            logger.info(
                "voice echo canceller: webrtc AEC3 (delay=%dms)",
                s.VOICE_EC_DELAY_MS,
            )
            return ec
