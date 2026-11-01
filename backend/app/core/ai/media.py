"""Thin media helpers over the gateway's OpenAI-compatible endpoints.

Return raw bytes / text — persisting to storage is the caller's job
(app.core.storage). Raises UpstreamError on gateway/provider failures so routers
get a clean 502 instead of an SDK traceback.
"""

import base64
from collections.abc import AsyncGenerator
from typing import Literal

import httpx
from openai import APIError, omit

from app.core.ai.client import fallbacks_extra_body, get_ai_client
from app.core.config import settings
from app.core.errors import UpstreamError
from app.core.logging import get_logger

logger = get_logger(__name__)


async def generate_image_bytes(prompt: str) -> bytes:
    """Generate an image for ``prompt``; return PNG bytes.

    Handles both response shapes: b64 payloads (gpt-image-*) and hosted URLs
    (dall-e and some gateways), downloading the latter so callers always get
    bytes to persist.
    """
    try:
        resp = await get_ai_client().images.generate(
            model=settings.AI_IMAGE_MODEL,
            prompt=prompt,
            size="1024x1024",
            extra_body=fallbacks_extra_body(settings.AI_IMAGE_FALLBACKS),
        )
    except APIError as e:
        logger.warning("image generation failed: %s", e)
        raise UpstreamError(f"image generation failed: {e.message}") from e

    if not resp.data:
        raise UpstreamError("image generation returned no data")
    item = resp.data[0]
    if item.b64_json:
        return base64.b64decode(item.b64_json)
    if item.url:
        try:
            async with httpx.AsyncClient(timeout=60) as http:
                r = await http.get(item.url)
                r.raise_for_status()
                return r.content
        except httpx.HTTPError as e:
            logger.warning("image download failed: %s", e)
            raise UpstreamError(f"image download failed: {e}") from e
    raise UpstreamError("image generation returned neither b64 nor url")


async def synthesize_speech_bytes(text: str) -> bytes:
    """Text-to-speech; return mp3 bytes."""
    try:
        resp = await get_ai_client().audio.speech.create(
            model=settings.AI_TTS_MODEL,
            voice="alloy",
            input=text,
            response_format="mp3",
            extra_body=fallbacks_extra_body(settings.AI_TTS_FALLBACKS),
        )
    except APIError as e:
        logger.warning("speech synthesis failed: %s", e)
        raise UpstreamError(f"speech synthesis failed: {e.message}") from e
    return resp.content


async def synthesize_speech_stream(
    text: str, response_format: Literal["pcm", "mp3"]
) -> AsyncGenerator[bytes, None]:
    """Streaming TTS: yields audio bytes as the gateway produces them.

    ``pcm`` is raw s16le 24kHz mono samples (no header — the voice WS pipes
    them straight to the client's audio worklet); ``mp3`` is a whole framed
    file the client decodes per clip. Voice mode prefers pcm and falls back
    per session if the gateway rejects it.
    """
    try:
        async with get_ai_client().audio.speech.with_streaming_response.create(
            model=settings.AI_TTS_MODEL,
            voice="alloy",
            input=text,
            response_format=response_format,
            extra_body=fallbacks_extra_body(settings.AI_TTS_FALLBACKS),
        ) as resp:
            async for chunk in resp.iter_bytes(chunk_size=8192):
                yield chunk
    except APIError as e:
        logger.warning("speech synthesis (%s) failed: %s", response_format, e)
        raise UpstreamError(f"speech synthesis failed: {e.message}") from e
    except httpx.HTTPError as e:
        # Mid-stream transport failures (read timeouts, resets) surface as
        # raw httpx errors, not APIError — same contract for the caller.
        logger.warning("speech synthesis (%s) failed mid-stream: %s", response_format, e)
        raise UpstreamError(f"speech synthesis failed: {e}") from e


async def transcribe_audio(data: bytes, filename: str, language: str | None = None) -> str:
    """Speech-to-text; return the transcript."""
    try:
        resp = await get_ai_client().audio.transcriptions.create(
            model=settings.AI_STT_MODEL,
            file=(filename, data),
            language=language or omit,
            extra_body=fallbacks_extra_body(settings.AI_STT_FALLBACKS),
        )
    except APIError as e:
        logger.warning("transcription failed: %s", e)
        raise UpstreamError(f"transcription failed: {e.message}") from e
    return resp.text


async def transcribe_audio_verbose(
    data: bytes, filename: str, language: str | None = None
) -> tuple[str, str | None]:
    """Speech-to-text returning (transcript, detected language ISO code).

    Voice mode uses the language both ways: a user selection is passed to
    the recognizer, while "auto" mode reads the detection back so the
    client can show what was heard.
    """
    try:
        resp = await get_ai_client().audio.transcriptions.create(
            model=settings.AI_STT_MODEL,
            file=(filename, data),
            response_format="verbose_json",
            language=language or omit,
            extra_body=fallbacks_extra_body(settings.AI_STT_FALLBACKS),
        )
    except APIError as e:
        logger.warning("transcription failed: %s", e)
        raise UpstreamError(f"transcription failed: {e.message}") from e
    return resp.text, getattr(resp, "language", None)
