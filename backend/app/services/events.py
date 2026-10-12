"""Agent activity events, delivered over Server-Sent Events.

Phase 1 uses an in-process broker: one asyncio queue per connected subscriber, with
per-user fan-out. That is correct for a single API process and keeps Redis out of the
Phase 1 dependency set. Phase 4 replaces the broker's internals with Redis pub/sub;
the publish and subscribe signatures here are what the rest of the application uses,
so that swap does not reach callers.

Two deliberate constraints:

* Queues are **bounded**. A browser tab that stops reading must not grow the server's
  memory without limit, so the oldest event is dropped instead.
* Events carry a short, user-facing description of what the agent is doing. They never
  carry chain-of-thought, prompts, or raw page content.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from app.core.clock import utcnow
from app.core.logging import get_correlation_id, get_logger

logger = get_logger(__name__)

#: Events buffered per subscriber before the oldest is dropped.
QUEUE_MAXSIZE: Final = 100

#: Sent when a connection opens, so a client can distinguish "connected, nothing
#: happening" from "not connected".
HELLO_EVENT: Final = "connected"


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    """One line of agent activity, as the user sees it."""

    kind: str
    message: str
    occurred_at: datetime
    correlation_id: str
    job_id: uuid.UUID | None = None
    application_id: uuid.UUID | None = None
    agent_run_id: uuid.UUID | None = None
    #: Small, redacted context: counts, state names, source names.
    data: dict[str, object] = field(default_factory=dict)

    def to_sse(self) -> str:
        """Serialise as an SSE frame."""
        payload = {
            "kind": self.kind,
            "message": self.message,
            "occurred_at": self.occurred_at.isoformat(),
            "correlation_id": self.correlation_id,
            "job_id": str(self.job_id) if self.job_id else None,
            "application_id": str(self.application_id) if self.application_id else None,
            "agent_run_id": str(self.agent_run_id) if self.agent_run_id else None,
            "data": self.data,
        }
        body = json.dumps(payload, separators=(",", ":"), default=str)
        return f"event: {self.kind}\ndata: {body}\n\n"


class ActivityBroker:
    """In-process per-user fan-out of activity events."""

    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue[ActivityEvent]]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, user_id: uuid.UUID, event: ActivityEvent) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(user_id, ()))
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest rather than block the publisher or grow without
                # bound. Activity events are informational; losing one is acceptable,
                # stalling a request to deliver it is not.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                    pass

    async def subscribe(self, user_id: uuid.UUID) -> asyncio.Queue[ActivityEvent]:
        queue: asyncio.Queue[ActivityEvent] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        async with self._lock:
            self._subscribers.setdefault(user_id, set()).add(queue)
        return queue

    async def unsubscribe(self, user_id: uuid.UUID, queue: asyncio.Queue[ActivityEvent]) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(user_id)
            if subscribers is None:
                return
            subscribers.discard(queue)
            if not subscribers:
                del self._subscribers[user_id]

    async def subscriber_count(self, user_id: uuid.UUID) -> int:
        async with self._lock:
            return len(self._subscribers.get(user_id, ()))


broker = ActivityBroker()


async def emit(
    *,
    user_id: uuid.UUID,
    kind: str,
    message: str,
    job_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    agent_run_id: uuid.UUID | None = None,
    data: dict[str, object] | None = None,
) -> None:
    """Publish one activity event to a user's open streams."""
    await broker.publish(
        user_id,
        ActivityEvent(
            kind=kind,
            message=message,
            occurred_at=utcnow(),
            correlation_id=get_correlation_id(),
            job_id=job_id,
            application_id=application_id,
            agent_run_id=agent_run_id,
            data=data or {},
        ),
    )


async def stream(user_id: uuid.UUID, *, keepalive_seconds: float = 20.0) -> AsyncIterator[str]:
    """Yield SSE frames for a user until the client disconnects.

    A comment frame is sent on idle so proxies and browsers do not time the
    connection out during a quiet period.
    """
    queue = await broker.subscribe(user_id)
    try:
        hello = ActivityEvent(
            kind=HELLO_EVENT,
            message="Connected to CareerPilot activity.",
            occurred_at=utcnow(),
            correlation_id=get_correlation_id(),
        )
        yield hello.to_sse()

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield event.to_sse()
    finally:
        await broker.unsubscribe(user_id, queue)
        logger.debug("activity_stream_closed", user_id=str(user_id))
