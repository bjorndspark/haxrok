"""Structured chat completions: the model answers in JSON that must match a
Pydantic model, enforced by the gateway as a strict ``json_schema``
response format, then validated again here.
"""

import time

import httpx
from openai import APIError
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

from app.core.ai.client import fallbacks_extra_body, get_ai_client
from app.core.config import settings
from app.core.errors import UpstreamError
from app.core.logging import get_logger

logger = get_logger(__name__)


async def complete_structured[T: BaseModel](
    output: type[T],
    messages: list[ChatCompletionMessageParam],
    *,
    name: str,
    timeout_s: float | None = None,
) -> T:
    """One chat completion with ``AI_CHAT_MODEL`` whose answer is parsed as
    ``output``. The model's JSON schema must already be strict-mode
    compatible (every property required, no extras, no defaults).

    ``timeout_s`` overrides ``AI_TIMEOUT_S`` for calls known to sit silent
    for longer (reasoning models that stream nothing until they finish).
    Raises ``UpstreamError`` on gateway failures and on output that doesn't
    match the schema.
    """
    start = time.monotonic()
    try:
        resp = await get_ai_client().chat.completions.create(
            model=settings.AI_CHAT_MODEL,
            messages=messages,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": name,
                    "strict": True,
                    "schema": output.model_json_schema(),
                },
            },
            timeout=timeout_s or settings.AI_TIMEOUT_S,
            extra_body=fallbacks_extra_body(settings.AI_CHAT_FALLBACKS),
        )
    except APIError as e:
        logger.warning("structured completion %s failed: %s", name, e)
        raise UpstreamError(f"The model call failed: {e.message}") from e
    except httpx.HTTPError as e:
        logger.warning("structured completion %s failed in transport: %s", name, e)
        raise UpstreamError(f"The model call failed: {e}") from e

    elapsed = time.monotonic() - start
    logger.info("structured completion %s took %.1fs, usage %s", name, elapsed, resp.usage)
    if not resp.choices or not resp.choices[0].message.content:
        raise UpstreamError("The model returned an empty answer.")
    content = resp.choices[0].message.content
    try:
        return output.model_validate_json(content)
    except ValidationError as e:
        logger.warning("structured completion %s didn't match its schema: %s", name, e)
        raise UpstreamError("The model's answer didn't match the expected format.") from e
