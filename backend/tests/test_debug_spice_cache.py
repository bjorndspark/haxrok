"""The vendor-model run cache: a repeated deck is served from the store, a
changed deck, model or ngspice runs again, and only finished runs are kept."""

import shutil

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.features.debug import spice_cache
from app.features.debug.spice import Ngspice, Run
from app.features.debug.spice_cache import CachedRunner, RedisRunStore


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def put(self, key: str, value: str) -> None:
        self.data[key] = value


class FakeNgspice:
    """Answers each deck with ``answer``, counting the runs."""

    def __init__(self, answer: Run | None, version: str | None = "ngspice-44.2") -> None:
        self.answer = answer
        self.banner = version
        self.runs = 0

    async def transient(self, netlist: str, files: dict[str, bytes]) -> Run | None:
        self.runs += 1
        return self.answer

    async def version(self) -> str | None:
        return self.banner


DONE = Run(0, "0 0\n1e-6 3.3\n", "pk_in = 1.5 at= 2e-6\n")


def test_the_key_changes_with_the_deck_the_models_and_ngspice():
    k = spice_cache.key("deck", {"a.lib": b"x"}, "ngspice-44.2")
    assert k.startswith("spice:run:") and k == spice_cache.key(
        "deck", {"a.lib": b"x"}, "ngspice-44.2"
    )
    assert k != spice_cache.key("deck2", {"a.lib": b"x"}, "ngspice-44.2")
    assert k != spice_cache.key("deck", {"a.lib": b"y"}, "ngspice-44.2")
    assert k != spice_cache.key("deck", {"b.lib": b"x"}, "ngspice-44.2")
    assert k != spice_cache.key("deck", {"a.lib": b"x"}, "ngspice-45")
    # Parts are length-prefixed: moving bytes between them changes the key.
    assert spice_cache.key("ab", {}, "v") != spice_cache.key("b", {}, "va")


def test_a_run_reads_back_as_stored():
    assert spice_cache.load(spice_cache.dump(DONE)) == DONE
    assert spice_cache.load("not base64 zlib") is None


async def test_a_repeated_deck_is_served_from_the_cache():
    inner, store = FakeNgspice(DONE), MemoryStore()
    runner = CachedRunner(inner, store)
    assert await runner.transient("deck", {"a.lib": b"x"}) == DONE
    assert await runner.transient("deck", {"a.lib": b"x"}) == DONE
    assert inner.runs == 1 and len(store.data) == 1
    await runner.transient("other deck", {"a.lib": b"x"})
    assert inner.runs == 2


@pytest.mark.parametrize(
    "answer",
    [
        Run(None, None, "partial"),  # timed out
        Run(1, None, "Error: unknown subckt"),  # failed
        None,  # ngspice isn't installed
    ],
)
async def test_a_timeout_or_failure_isnt_kept(answer: Run | None):
    inner, store = FakeNgspice(answer), MemoryStore()
    runner = CachedRunner(inner, store)
    await runner.transient("deck", {})
    await runner.transient("deck", {})
    assert inner.runs == 2 and store.data == {}


async def test_without_a_version_or_with_ttl_zero_the_cache_is_bypassed(
    monkeypatch: pytest.MonkeyPatch,
):
    inner, store = FakeNgspice(DONE, version=None), MemoryStore()
    await CachedRunner(inner, store).transient("deck", {})
    assert store.data == {}

    monkeypatch.setattr(spice_cache.settings, "SPICE_CACHE_TTL_S", 0)
    inner, store = FakeNgspice(DONE), MemoryStore()
    runner = CachedRunner(inner, store)
    await runner.transient("deck", {})
    await runner.transient("deck", {})
    assert inner.runs == 2 and store.data == {}


class FakeRedis:
    def __init__(self, broken: bool = False) -> None:
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.broken = broken

    async def get(self, key: str) -> str | None:
        if self.broken:
            raise RedisConnectionError("down")
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int) -> None:
        if self.broken:
            raise RedisConnectionError("down")
        self.data[key] = value
        self.ttl[key] = ex


async def test_redis_keeps_runs_for_the_ttl_and_an_outage_runs_ngspice(
    monkeypatch: pytest.MonkeyPatch,
):
    redis = FakeRedis()
    monkeypatch.setattr(spice_cache, "get_redis", lambda: redis)
    inner = FakeNgspice(DONE)
    runner = CachedRunner(inner, RedisRunStore())
    await runner.transient("deck", {})
    assert await runner.transient("deck", {}) == DONE and inner.runs == 1
    assert list(redis.ttl.values()) == [spice_cache.settings.SPICE_CACHE_TTL_S]

    redis.broken = True
    assert await runner.transient("deck", {}) == DONE and inner.runs == 2


@pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice isn't installed")
async def test_ngspice_says_its_version():
    got = await Ngspice().version()
    assert got is not None and "ngspice" in got
