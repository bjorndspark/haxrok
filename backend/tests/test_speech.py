"""Speech endpoints (push-to-talk transcription, read-aloud synthesis).
The gateway is faked."""

from httpx import AsyncClient

from app.core.config import settings
from app.features.speech import router as speech_router


async def test_transcribe_returns_text(client: AsyncClient, monkeypatch):
    async def fake_transcribe(data: bytes, filename: str, language=None):
        assert data == b"audio"
        return "hello"

    monkeypatch.setattr(speech_router.ai_media, "transcribe_audio", fake_transcribe)
    resp = await client.post(
        "/api/v1/speech/transcribe", files={"file": ("a.webm", b"audio", "audio/webm")}
    )
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "data": {"text": "hello"}}


async def test_transcribe_rejects_oversized_upload(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_MAX_BYTES", 10)
    resp = await client.post(
        "/api/v1/speech/transcribe", files={"file": ("a.webm", b"x" * 11, "audio/webm")}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "MEDIA_TOO_LARGE"


async def test_synthesize_saves_mp3(client: AsyncClient, monkeypatch, tmp_path):
    async def fake_synth(text: str) -> bytes:
        return b"ID3mp3"

    monkeypatch.setattr(speech_router.ai_media, "synthesize_speech_bytes", fake_synth)
    monkeypatch.setattr(settings, "MEDIA_DIR", str(tmp_path))
    resp = await client.post("/api/v1/speech/synthesize", json={"text": "hi"})

    assert resp.status_code == 200
    url = resp.json()["data"]["url"]
    assert url.startswith("/media/files/") and url.endswith(".mp3")
    assert (tmp_path / url.rsplit("/", 1)[1]).read_bytes() == b"ID3mp3"
