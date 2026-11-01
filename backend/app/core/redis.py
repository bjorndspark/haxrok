"""Shared async Redis client — one lazily-created connection pool per process.
Redis is shared infra (docker-compose.infra.yml); the URL comes from
REDIS_URL, which compose overrides with the internal service name."""

from urllib.parse import urlsplit

import redis.asyncio as redis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """Return the process-wide Redis client, connecting on first use."""
    global _client
    if _client is None:
        _client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        logger.info("Redis client created for %s", _redact(settings.REDIS_URL))
    return _client


def _redact(url: str) -> str:
    """The URL without credentials — managed Redis URLs carry a password."""
    u = urlsplit(url)
    host = f"{u.hostname}:{u.port}" if u.port else (u.hostname or "")
    return f"{u.scheme}://{host}{u.path}"
