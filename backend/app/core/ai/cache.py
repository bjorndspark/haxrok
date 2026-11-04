"""Answer cache for gateway calls, in Redis. The same request (model,
fallbacks, messages, response format) returns the stored answer instead of
calling the model again, for ``AI_CACHE_TTL_S`` seconds (0 turns it off).

It fails open: a Redis outage is logged and the call goes to the model as
if nothing was cached.
"""

import hashlib
import json
from typing import Any

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

_PREFIX = "ai:completion:"


def key(request: dict[str, Any]) -> str:
    """A Redis key for one request: a hash of its canonical JSON, so any
    change to the prompt, schema or model is a different entry."""
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), default=str)
    return _PREFIX + hashlib.sha256(canonical.encode()).hexdigest()


async def get(cache_key: str) -> str | None:
    """The stored answer, or None on a miss, with caching off, or when Redis
    is unreachable."""
    if settings.AI_CACHE_TTL_S <= 0:
        return None
    try:
        value = await get_redis().get(cache_key)
    except RedisError as e:
        logger.warning("AI cache read failed, calling the model: %s", e)
        return None
    # The client decodes responses, so a hit is already a str.
    return value if isinstance(value, str) else None


async def put(cache_key: str, answer: str) -> None:
    """Store an answer for ``AI_CACHE_TTL_S`` seconds; errors are logged only."""
    if settings.AI_CACHE_TTL_S <= 0:
        return
    try:
        await get_redis().set(cache_key, answer, ex=settings.AI_CACHE_TTL_S)
    except RedisError as e:
        logger.warning("AI cache write failed: %s", e)
