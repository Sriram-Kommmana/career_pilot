"""Time, as an injected dependency.

The domain layer must be testable without freezing the system clock, so every pure
function that needs "now" takes it as an argument. This module is the only place the
real clock is read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Anything that can tell the time in UTC."""

    def now(self) -> datetime: ...


class SystemClock:
    """The real clock. Always timezone-aware UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock pinned to one instant, for tests."""

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._instant = instant.astimezone(UTC)

    def now(self) -> datetime:
        return self._instant

    def advance(self, *, seconds: float) -> None:
        from datetime import timedelta

        self._instant += timedelta(seconds=seconds)


system_clock = SystemClock()


def utcnow() -> datetime:
    """Convenience for service and API code. Domain code takes a clock instead."""
    return system_clock.now()
