"""The in-process connector rate limiter.

Uses an injected fake clock and a recording fake ``sleep`` so the test asserts on
timing deterministically — no real wall-clock delay, no flakiness.
"""

from __future__ import annotations

import pytest

from app.connectors.base import RateLimiter

pytestmark = pytest.mark.unit


class FakeClock:
    """A clock that only advances when told to."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingSleeper:
    """Records every requested delay; advances the paired clock to simulate it."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.clock.advance(seconds)


class TestRateLimiter:
    async def test_the_first_call_never_waits(self) -> None:
        clock = FakeClock()
        sleeper = RecordingSleeper(clock)
        limiter = RateLimiter(requests_per_minute=30, clock=clock)

        await limiter.wait(sleep=sleeper)

        assert sleeper.calls == []

    async def test_a_second_call_too_soon_waits_the_remaining_interval(self) -> None:
        clock = FakeClock()
        sleeper = RecordingSleeper(clock)
        # 30/minute => a 2 second minimum interval between requests.
        limiter = RateLimiter(requests_per_minute=30, clock=clock)

        await limiter.wait(sleep=sleeper)
        clock.advance(0.5)  # only half a second has passed
        await limiter.wait(sleep=sleeper)

        assert sleeper.calls == pytest.approx([1.5])

    async def test_a_call_after_the_interval_has_already_elapsed_does_not_wait(self) -> None:
        clock = FakeClock()
        sleeper = RecordingSleeper(clock)
        limiter = RateLimiter(requests_per_minute=30, clock=clock)

        await limiter.wait(sleep=sleeper)
        clock.advance(5.0)  # well past the 2 second minimum interval
        await limiter.wait(sleep=sleeper)

        assert sleeper.calls == []

    async def test_three_rapid_calls_each_wait_their_own_remaining_interval(self) -> None:
        clock = FakeClock()
        sleeper = RecordingSleeper(clock)
        limiter = RateLimiter(requests_per_minute=60, clock=clock)  # 1 second interval

        await limiter.wait(sleep=sleeper)
        await limiter.wait(sleep=sleeper)
        await limiter.wait(sleep=sleeper)

        assert sleeper.calls == pytest.approx([1.0, 1.0])

    def test_a_non_positive_rate_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            RateLimiter(requests_per_minute=0)
        with pytest.raises(ValueError, match="positive"):
            RateLimiter(requests_per_minute=-5)

    def test_requests_per_minute_is_exposed_for_cache_invalidation(self) -> None:
        """app.services.discovery caches one limiter per connector kind and rebuilds
        it if the configured rate changes; that logic reads this attribute."""
        limiter = RateLimiter(requests_per_minute=42)
        assert limiter.requests_per_minute == 42
