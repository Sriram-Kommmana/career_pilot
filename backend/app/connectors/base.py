"""Shared connector infrastructure: errors, politeness, and the connector contract.

Every connector failure becomes one of the typed errors below, never a raw
exception leaking out of ``app.services.discovery``. That is what lets the service
layer record a clean, specific ``JobSource.last_error`` and keep going with the next
source instead of letting one bad response take down the whole discovery run.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.domain.normalize import NormalizedJob


class ConnectorError(Exception):
    """Base class for a connector failure. Always carries a human-readable reason."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        #: Whether retrying the same request might succeed (a timeout or a 5xx), as
        #: opposed to a failure that will recur every time (a 404 for a bad board
        #: token, or a response that doesn't parse).
        self.retryable = retryable


class ConnectorTimeoutError(ConnectorError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=True)


class ConnectorHTTPError(ConnectorError):
    """The source responded, but with an error status."""

    def __init__(self, message: str, *, status_code: int) -> None:
        # 5xx and 429 are worth retrying; a 4xx that isn't a rate limit (404 for an
        # unknown board token, 400 for a malformed request) will not succeed on retry.
        retryable = status_code >= 500 or status_code == 429
        super().__init__(message, retryable=retryable)
        self.status_code = status_code


class ConnectorParseError(ConnectorError):
    """The response came back 200 OK but was not shaped the way we expected.

    Never retryable: the source answered successfully, so trying again immediately
    will get the same malformed shape back.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


class Connector(Protocol):
    """What every connector implements.

    ``kind`` matches ``JobSource.kind`` and the ``source`` field on every
    ``NormalizedJob`` it produces — the thing that lets ingestion, deduplication,
    and the UI's source badge all agree on what produced a row.
    """

    kind: str

    async def fetch_jobs(self, external_key: str) -> list[NormalizedJob]: ...


class RateLimiter:
    """A simple in-process minimum-interval gate, shared across calls to one host.

    Phase 2 runs discovery synchronously, inline in one request, so an in-process
    gate is the right amount of machinery. A cross-process limiter (needed once
    Phase 4 adds worker processes that could run concurrently) is explicitly out of
    scope here — see ``docs/PLAN.md`` Phase 4.
    """

    def __init__(
        self, *, requests_per_minute: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive.")
        self.requests_per_minute = requests_per_minute
        self._min_interval = 60.0 / requests_per_minute
        self._clock = clock
        self._last_call: float | None = None

    async def wait(self, *, sleep: Callable[[float], Awaitable[None]]) -> None:
        """Block until it is polite to make the next request.

        ``sleep`` is injected (rather than importing ``asyncio.sleep`` directly) so
        a unit test can assert on timing without an actual wall-clock delay.
        """
        now = self._clock()
        if self._last_call is not None:
            elapsed = now - self._last_call
            remaining = self._min_interval - elapsed
            if remaining > 0:
                await sleep(remaining)
                now = self._clock()
        self._last_call = now
