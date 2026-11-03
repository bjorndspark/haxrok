"""Chat completions with ``AI_CHAT_MODEL``: the one place that calls the
gateway, turns its failures into ``UpstreamError`` and logs timing and usage.
``complete_text`` returns the answer as written; ``structured`` builds on
``complete`` for answers that must match a schema.
"""

import time
from typing import Any

import httpx
from openai import APIError
from openai.types.chat import ChatCompletionMessageParam

from app.core.ai.client import fallbacks_extra_body, get_ai_client
from app.core.config import settings
from app.core.errors import UpstreamError
from app.core.logging import get_logger

logger = get_logger(__name__)


async def complete(
    messages: list[ChatCompletionMessageParam],
    *,
    name: str,
    timeout_s: float | None = None,
    response_format: Any = None,
) -> str:
    """One chat completion; returns the answer's text. ``name`` labels the
    call in logs and errors. ``timeout_s`` overrides ``AI_TIMEOUT_S`` for
    calls known to sit silent for longer (reasoning models that stream
    nothing until they finish). Raises ``UpstreamError`` on gateway failures
    and empty answers."""
    start = time.monotonic()
    extra: dict[str, Any] = {}
    if response_format is not None:
        extra["response_format"] = response_format
    try:
        resp = await get_ai_client().chat.completions.create(
            model=settings.AI_CHAT_MODEL,
            messages=messages,
            timeout=timeout_s or settings.AI_TIMEOUT_S,
            extra_body=fallbacks_extra_body(settings.AI_CHAT_FALLBACKS),
            **extra,
        )
    except APIError as e:
        logger.warning("completion %s failed: %s", name, e)
        raise UpstreamError(f"The model call failed: {e.message}") from e
    except httpx.HTTPError as e:
        logger.warning("completion %s failed in transport: %s", name, e)
        raise UpstreamError(f"The model call failed: {e}") from e

    logger.info("completion %s took %.1fs, usage %s", name, time.monotonic() - start, resp.usage)
    if not resp.choices or not resp.choices[0].message.content:
        raise UpstreamError("The model returned an empty answer.")
    return resp.choices[0].message.content


async def complete_text(
    messages: list[ChatCompletionMessageParam],
    *,
    name: str,
    timeout_s: float | None = None,
) -> str:
    """A free-text answer, no response format imposed. Raises
    ``UpstreamError`` like ``complete``."""
    return await complete(messages, name=name, timeout_s=timeout_s)
