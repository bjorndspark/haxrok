"""Pluggable wall-clock for business-meaningful time.

All business wall-clock reads go through ``get_clock().now()`` so tests can
install a ``FakeClock`` for deterministic time. Latency measurements stay on
the monotonic clock — a wall clock is the wrong tool for durations.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Wall clock for business time. ``now()`` is always tz-aware UTC."""

    def now(self) -> datetime:
        """Return the current instant, tz-aware UTC."""
        ...


class SystemClock:
    """Production clock backed by the OS wall clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """Deterministic clock for tests. Fixed at construction; mutate via
    ``set`` / ``advance``."""

    def __init__(self, fixed: datetime) -> None:
        self._now = _require_aware(fixed)

    def now(self) -> datetime:
        return self._now

    def set(self, dt: datetime) -> None:
        self._now = _require_aware(dt)

    def advance(self, delta: timedelta) -> None:
        self._now = _require_aware(self._now + delta)


def _require_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("FakeClock requires a tz-aware datetime (use UTC)")
    return dt


_clock: Clock = SystemClock()


def get_clock() -> Clock:
    """Return the active clock (``SystemClock`` in production)."""
    return _clock


def set_clock(clock: Clock) -> None:
    """Install a clock. Test seam only — never call from request paths."""
    global _clock
    _clock = clock
