"""Speech I/O endpoints for the chat UI: push-to-talk transcription and
read-aloud synthesis. Image generation is a chat concern — it lives in
features/chat (the reply lands in conversation history), not here."""

from fastapi import APIRouter, Form, UploadFile

from app.core.ai import media as ai_media
from app.core.config import settings
from app.core.errors import BadRequestError
from app.core.responses import SuccessResponse, ok
from app.core.storage import save_media
from app.features.speech.schemas import SynthesizeRequest, SynthesizeResponse, TranscribeResponse

router = APIRouter(prefix="/speech", tags=["speech"])


@router.post("/transcribe")
async def transcribe(
    file: UploadFile, language: str | None = Form(None)
) -> SuccessResponse[TranscribeResponse]:
    """Audio upload (mic recording) → transcript text."""
    # Reject on the declared size first, then cap the read itself — the
    # declared size is client-supplied and can't be trusted alone.
    if file.size is not None and file.size > settings.MEDIA_MAX_BYTES:
        raise BadRequestError("audio too large", error_code="MEDIA_TOO_LARGE")
    data = await file.read(settings.MEDIA_MAX_BYTES + 1)
    if not data:
        raise BadRequestError("empty audio upload")
    if len(data) > settings.MEDIA_MAX_BYTES:
        raise BadRequestError("audio too large", error_code="MEDIA_TOO_LARGE")
    text = await ai_media.transcribe_audio(data, file.filename or "audio.webm", language=language)
    return ok(TranscribeResponse(text=text))


@router.post("/synthesize")
async def synthesize(body: SynthesizeRequest) -> SuccessResponse[SynthesizeResponse]:
    """Text → spoken audio file; returns a URL the client can play."""
    data = await ai_media.synthesize_speech_bytes(body.text)
    return ok(SynthesizeResponse(url=await save_media(data, "mp3")))
