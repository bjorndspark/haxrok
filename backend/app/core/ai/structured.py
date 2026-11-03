"""Structured chat completions: the model answers in JSON that must match a
Pydantic model, enforced by the gateway as a strict ``json_schema``
response format, then validated again here.
"""

from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

from app.core.ai.chat import complete
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

    ``timeout_s`` is as in ``chat.complete``. Raises ``UpstreamError`` on
    gateway failures and on output that doesn't match the schema.
    """
    content = await complete(
        messages,
        name=name,
        timeout_s=timeout_s,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": output.model_json_schema()},
        },
    )
    try:
        return output.model_validate_json(content)
    except ValidationError as e:
        logger.warning("structured completion %s didn't match its schema: %s", name, e)
        raise UpstreamError("The model's answer didn't match the expected format.") from e
