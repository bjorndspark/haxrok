"""The gateway answer cache: a repeated request skips the model, a changed
one doesn't, and a Redis outage falls through to the model."""

from types import SimpleNamespace

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.ai import cache, chat
from app.core.config import settings


class FakeRedis:
    def __init__(self, broken: bool = False) -> None:
        self.data: dict[str, str] = {}
        self.broken = broken

    async def get(self, key: str) -> str | None:
        if self.broken:
            raise RedisConnectionError("down")
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int) -> None:
        if self.broken:
            raise RedisConnectionError("down")
        self.data[key] = value


class FakeGateway:
    def __init__(self) -> None:
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **_: object) -> SimpleNamespace:
        self.calls += 1
        message = SimpleNamespace(content=f"answer {self.calls}")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


@pytest.fixture
def gateway(monkeypatch) -> FakeGateway:
    gw = FakeGateway()
    monkeypatch.setattr(chat, "get_ai_client", lambda: gw)
    return gw


def _msgs(text: str) -> list:
    return [{"role": "user", "content": text}]


async def test_repeated_request_is_served_from_cache(gateway, monkeypatch) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(cache, "get_redis", lambda: redis)
    first = await chat.complete_text(_msgs("same"), name="t")
    second = await chat.complete_text(_msgs("same"), name="t")
    assert first == second == "answer 1"
    assert gateway.calls == 1


async def test_changed_prompt_or_schema_calls_the_model(gateway, monkeypatch) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(cache, "get_redis", lambda: redis)
    await chat.complete(_msgs("a"), name="t")
    await chat.complete(_msgs("b"), name="t")
    await chat.complete(_msgs("a"), name="t", response_format={"type": "json_object"})
    assert gateway.calls == 3


async def test_ttl_zero_turns_caching_off(gateway, monkeypatch) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(cache, "get_redis", lambda: redis)
    monkeypatch.setattr(settings, "AI_CACHE_TTL_S", 0)
    await chat.complete_text(_msgs("same"), name="t")
    await chat.complete_text(_msgs("same"), name="t")
    assert gateway.calls == 2
    assert redis.data == {}


async def test_redis_outage_falls_through_to_the_model(gateway, monkeypatch) -> None:
    monkeypatch.setattr(cache, "get_redis", lambda: FakeRedis(broken=True))
    assert await chat.complete_text(_msgs("same"), name="t") == "answer 1"
    assert await chat.complete_text(_msgs("same"), name="t") == "answer 2"
