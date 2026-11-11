"""Vendor-model runs cached in Redis (plan 006 step 4).

A start-up run of a switching model takes minutes, and a repeated review
of the same board runs the same decks again. ``CachedRunner`` wraps a
``ModelRunner`` and keeps ngspice's raw ``Run`` (the waveform and the log),
not the verdict, so a verdict that changes later still applies to a cached
run. The key is a sha256 of the deck, the model files' names and bytes and
ngspice's version banner; entries expire after ``SPICE_CACHE_TTL_S`` (0
turns the cache off). Only finished runs are kept: a timeout or a failure
runs again next time.

It fails open, like ``app.core.ai.cache``: a Redis outage is logged and the
run goes to ngspice as if nothing was cached.
"""

import base64
import hashlib
import json
import zlib
from typing import Protocol

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.features.debug.spice import ModelRunner, Run

logger = get_logger(__name__)

_PREFIX = "spice:run:"


class RunStore(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def put(self, key: str, value: str) -> None: ...


class RedisRunStore:
    """Redis, for ``SPICE_CACHE_TTL_S`` seconds; errors are logged only."""

    async def get(self, key: str) -> str | None:
        try:
            value = await get_redis().get(key)
        except RedisError as e:
            logger.warning("spice cache read failed, running ngspice: %s", e)
            return None
        return value if isinstance(value, str) else None

    async def put(self, key: str, value: str) -> None:
        try:
            await get_redis().set(key, value, ex=settings.SPICE_CACHE_TTL_S)
        except RedisError as e:
            logger.warning("spice cache write failed: %s", e)


class VersionedRunner(ModelRunner, Protocol):
    async def version(self) -> str | None:
        """ngspice's version banner; None if it isn't installed."""
        ...


def key(netlist: str, files: dict[str, bytes], version: str) -> str:
    """The run's key: each part length-prefixed, so no two different runs
    hash the same bytes."""
    h = hashlib.sha256()
    for chunk in (
        version.encode(),
        netlist.encode(),
        *(x for name in sorted(files) for x in (name.encode(), files[name])),
    ):
        h.update(len(chunk).to_bytes(8, "big"))
        h.update(chunk)
    return _PREFIX + h.hexdigest()


def dump(run: Run) -> str:
    """A run as stored: JSON, compressed (a waveform is megabytes of
    text), base64 since the client decodes replies as str."""
    raw = json.dumps({"code": run.code, "data": run.data, "log": run.log}).encode()
    return base64.b64encode(zlib.compress(raw)).decode("ascii")


def load(text: str) -> Run | None:
    """A stored run, or None when the entry doesn't read back."""
    try:
        got = json.loads(zlib.decompress(base64.b64decode(text)))
        return Run(got["code"], got["data"], got["log"])
    except (ValueError, KeyError, TypeError, zlib.error) as e:
        logger.warning("spice cache entry unreadable, running ngspice: %s", e)
        return None


class CachedRunner:
    """A ``ModelRunner`` that serves a deck it has run before from the store."""

    def __init__(self, inner: VersionedRunner, store: RunStore | None = None) -> None:
        self.inner = inner
        self.store = store or RedisRunStore()
        self._version: str | None = None

    async def transient(self, netlist: str, files: dict[str, bytes]) -> Run | None:
        """The cached run, else ngspice's, kept when it finished."""
        if settings.SPICE_CACHE_TTL_S <= 0:
            return await self.inner.transient(netlist, files)
        if self._version is None:
            self._version = await self.inner.version()
        if self._version is None:
            return await self.inner.transient(netlist, files)  # not installed
        k = key(netlist, files, self._version)
        if (text := await self.store.get(k)) is not None and (run := load(text)) is not None:
            logger.info("spice cache: served %s from the cache", k[len(_PREFIX) :][:12])
            return run
        run = await self.inner.transient(netlist, files)
        if run is not None and run.code == 0 and run.data is not None:
            await self.store.put(k, dump(run))
        return run
