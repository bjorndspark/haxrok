"""Voice-mode smoke test against the live stack.

Synthesizes speech via the gateway (TTS pcm 24kHz), resamples to the
16kHz s16le mic format, streams it into the voice WebSocket in realtime
chunks, and prints every event/binary frame that comes back.

Run inside the backend container:  uv run python scripts/smoke/voice.py
Or from repo root via:             ./scripts/docker/uv.sh run python scripts/smoke/voice.py
"""

import asyncio
import json
import struct
import sys
import urllib.request

import websockets

GATEWAY = "http://host.docker.internal:8080/v1"
BACKEND = "http://localhost:8000"
PHRASE = sys.argv[1] if len(sys.argv) > 1 else "Hello, this is a voice pipeline smoke test."


def tts_pcm() -> bytes:
    req = urllib.request.Request(
        f"{GATEWAY}/audio/speech",
        data=json.dumps(
            {
                "model": "tts-1",
                "input": PHRASE,
                "voice": "alloy",
                "response_format": "pcm",
            }
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(req, timeout=60).read()


def resample_24k_to_16k(pcm: bytes) -> bytes:
    src = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    out = [src[min(int(i * 1.5), len(src) - 1)] for i in range(int(len(src) * 2 / 3))]
    return struct.pack(f"<{len(out)}h", *out)


def create_conversation() -> int:
    req = urllib.request.Request(
        f"{BACKEND}/api/v1/chat/conversations",
        data=b'{"title": "voice smoke"}',
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req).read())["data"]["id"]


async def main() -> int:
    pcm16 = resample_24k_to_16k(tts_pcm())
    cid = create_conversation()
    print(f"conversation={cid} audio={len(pcm16)}B ({len(pcm16) / 32000:.1f}s)")

    async with websockets.connect(
        f"{BACKEND.replace('http', 'ws')}/api/v1/chat/conversations/{cid}/voice"
    ) as ws:
        await ws.send('{"type":"speech_start"}')
        for i in range(0, len(pcm16), 2048):
            await ws.send(pcm16[i : i + 2048])
            await asyncio.sleep(2048 / 64000)  # ~realtime pacing
        # Mirror the client: ~0.5s of quiet → speech_maybe_end, then the
        # VAD redemption (1.2s) confirms with speech_end ~0.7s later. The
        # speculative turn's output is held until that confirmation.
        await asyncio.sleep(0.5)
        await ws.send('{"type":"speech_maybe_end"}')
        await asyncio.sleep(0.7)
        await ws.send('{"type":"speech_end"}')
        try:
            async with asyncio.timeout(45):
                async for msg in ws:
                    if isinstance(msg, bytes):
                        print(f"  <binary {len(msg)}B>")
                    else:
                        print(" ", msg)
                        if '"turn_end"' in msg:
                            return 0
        except TimeoutError:
            print("TIMEOUT waiting for turn_end")
            return 1
    return 0


sys.exit(asyncio.run(main()))
