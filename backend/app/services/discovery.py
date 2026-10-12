"""Running a connector and recording what happened.

This is the only place a connector's output reaches the database. It is
responsible for everything a single bad response must not be allowed to do:

* **Corrupt existing data.** A connector failure (timeout, malformed JSON, a
  single bad job entry) stops that one source's processing and is recorded on
  ``JobSource``; it never touches rows already in the database, including rows
  from a previous, successful run of the same source.
* **Delete anything.** A posting that stops appearing in a source's listing is
  marked ``SourceStatus.CLOSED`` — the row stays, it simply stops being an open
  opportunity, exactly like the existing ``JOB_CLOSED`` hard filter already
  expects. Nothing is ever deleted by discovery.
* **Touch demo data.** Every write here goes through the same ``upsert_job`` path
  as the seed script, with ``is_demo_data=False``; the closed-job sweep is scoped
  to one real ``JobSource`` and explicitly excludes demo rows.

Retries and rate limiting happen one level down, inside the connector; this layer
treats a connector call as a single bounded operation that either returns a list of
postings or raises one of ``app.connectors.base``'s typed errors.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.asyncio import AsyncRetrying

from app.connectors.base import Connector, ConnectorError, RateLimiter
from app.connectors.greenhouse import SOURCE_KIND as GREENHOUSE_KIND
from app.connectors.greenhouse import GreenhouseConnector
from app.core.clock import utcnow
from app.core.config import Settings
from app.core.logging import get_correlation_id, get_logger
from app.domain.normalize import SourceStatus
from app.models import AgentRun, Job, JobSource, ToolExecution
from app.services import events as events_service
from app.services.jobs import get_or_create_source, upsert_job

logger = get_logger(__name__)

#: Shared across one discovery run so multiple boards are still rate-limited
#: against each other, not just against themselves.
_rate_limiters: dict[str, RateLimiter] = {}


def _rate_limiter_for(kind: str, *, requests_per_minute: int) -> RateLimiter:
    limiter = _rate_limiters.get(kind)
    if limiter is None or limiter.requests_per_minute != requests_per_minute:
        limiter = RateLimiter(requests_per_minute=requests_per_minute)
        _rate_limiters[kind] = limiter
    return limiter


@dataclass(frozen=True, slots=True)
class SourceDiscoveryResult:
    """What happened running one board through one connector."""

    kind: str
    external_key: str
    status: Literal["ok", "failed"]
    jobs_seen: int = 0
    jobs_created: int = 0
    jobs_updated: int = 0
    jobs_closed: int = 0
    error: str | None = None
    duration_ms: int = 0
    retry_count: int = 0


@dataclass(frozen=True, slots=True)
class DiscoveryRunResult:
    agent_run_id: uuid.UUID
    sources: tuple[SourceDiscoveryResult, ...] = field(default_factory=tuple)


def _build_connector(kind: str, settings: Settings) -> Connector:
    if kind == GREENHOUSE_KIND:
        return GreenhouseConnector(
            user_agent=settings.connector_user_agent,
            timeout_seconds=settings.connector_timeout_seconds,
        )
    raise ValueError(f"No connector implemented for kind {kind!r}.")


async def run_discovery(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    settings: Settings,
    board_tokens: list[str],
) -> DiscoveryRunResult:
    """Run Greenhouse discovery for the given board tokens.

    An empty ``board_tokens`` list means "re-run every currently enabled Greenhouse
    source already configured", rather than doing nothing — so the same endpoint
    serves both "try this new board" and "refresh what I already have" without two
    separate routes.
    """
    kind = GREENHOUSE_KIND
    connector = _build_connector(kind, settings)
    limiter = _rate_limiter_for(kind, requests_per_minute=settings.connector_rate_limit_per_minute)

    tokens = [t.strip() for t in board_tokens if t.strip()]
    if not tokens:
        tokens = await _enabled_external_keys(session, kind=kind)

    agent_run = AgentRun(
        user_id=user_id,
        kind="discover",
        status="running",
        correlation_id=get_correlation_id(),
        context={"connector": kind, "board_tokens": tokens},
    )
    session.add(agent_run)
    await session.flush()

    await events_service.emit(
        user_id=user_id,
        kind="discovery_started",
        message=f"Searching {len(tokens)} Greenhouse board(s)."
        if tokens
        else "No Greenhouse boards configured yet.",
        agent_run_id=agent_run.id,
        data={"board_tokens": tokens},
    )

    results: list[SourceDiscoveryResult] = []
    for sequence, token in enumerate(tokens, start=1):
        await limiter.wait(sleep=asyncio.sleep)
        result = await _run_one_source(
            session,
            connector=connector,
            kind=kind,
            external_key=token,
            agent_run_id=agent_run.id,
            sequence=sequence,
            user_id=user_id,
            max_retries=settings.connector_max_retries,
        )
        results.append(result)

    succeeded = sum(1 for r in results if r.status == "ok")
    # Only a *total* failure (at least one source was attempted, and none of them
    # worked) marks the run itself "failed". A partial failure — some sources
    # succeeded, one bad board token among several — still means the run did its
    # job; the per-source detail is what tells the caller which board failed.
    all_attempted_failed = bool(results) and succeeded == 0
    agent_run.status = "failed" if all_attempted_failed else "succeeded"
    agent_run.finished_at = utcnow()
    agent_run.duration_ms = int(
        (agent_run.finished_at - agent_run.started_at).total_seconds() * 1000
    )
    agent_run.summary = (
        f"Checked {len(results)} source(s): "
        f"{sum(r.jobs_created for r in results)} new, "
        f"{sum(r.jobs_updated for r in results)} updated, "
        f"{sum(r.jobs_closed for r in results)} closed, "
        f"{sum(1 for r in results if r.status == 'failed')} failed."
    )
    await session.flush()

    await events_service.emit(
        user_id=user_id,
        kind="discovery_finished",
        message=agent_run.summary,
        agent_run_id=agent_run.id,
    )

    return DiscoveryRunResult(agent_run_id=agent_run.id, sources=tuple(results))


async def _enabled_external_keys(session: AsyncSession, *, kind: str) -> list[str]:
    result = await session.execute(
        select(JobSource.external_key).where(JobSource.kind == kind, JobSource.is_enabled.is_(True))
    )
    return [row[0] for row in result.all()]


async def _run_one_source(
    session: AsyncSession,
    *,
    connector: Connector,
    kind: str,
    external_key: str,
    agent_run_id: uuid.UUID,
    sequence: int,
    user_id: uuid.UUID,
    max_retries: int,
) -> SourceDiscoveryResult:
    started = time.monotonic()
    source = await get_or_create_source(session, kind=kind, external_key=external_key)
    source.last_run_at = utcnow()

    await events_service.emit(
        user_id=user_id,
        kind="discovery_source_started",
        message=f"Fetching postings from {kind}:{external_key}.",
        agent_run_id=agent_run_id,
        data={"source": external_key},
    )

    retry_count = 0

    def _count_retry(_retry_state: object) -> None:
        nonlocal retry_count
        retry_count += 1

    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max(1, max_retries + 1)),
            wait=wait_exponential_jitter(multiplier=0.5, max=8.0),
            retry=retry_if_exception(lambda exc: isinstance(exc, ConnectorError) and exc.retryable),
            after=_count_retry,
            reraise=True,
        ):
            with attempt:
                postings = await connector.fetch_jobs(external_key)
    except ConnectorError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        source.last_error = exc.message
        source.consecutive_failures += 1
        await _record_tool_execution(
            session,
            agent_run_id=agent_run_id,
            sequence=sequence,
            tool_name=f"{kind}.fetch_jobs",
            status="failed",
            duration_ms=duration_ms,
            arguments={"external_key": external_key},
            error=exc.message,
        )
        await session.flush()
        await events_service.emit(
            user_id=user_id,
            kind="discovery_source_failed",
            message=f"Could not fetch {kind}:{external_key}: {exc.message}",
            agent_run_id=agent_run_id,
            data={"source": external_key, "error": exc.message},
        )
        logger.warning(
            "connector_run_failed",
            kind=kind,
            external_key=external_key,
            error=exc.message,
            retryable=exc.retryable,
            retry_count=retry_count,
        )
        return SourceDiscoveryResult(
            kind=kind,
            external_key=external_key,
            status="failed",
            error=exc.message,
            duration_ms=duration_ms,
            retry_count=retry_count,
        )

    created = 0
    updated = 0
    seen_source_job_ids: set[str] = set()
    for normalized in postings:
        seen_source_job_ids.add(normalized.source_job_id)
        _, was_created = await upsert_job(
            session, normalized=normalized, source_external_key=external_key, is_demo_data=False
        )
        if was_created:
            created += 1
        else:
            updated += 1

    closed = await _close_missing_jobs(
        session, job_source_id=source.id, seen_source_job_ids=seen_source_job_ids
    )

    source.last_success_at = utcnow()
    source.last_error = None
    source.consecutive_failures = 0
    await session.flush()

    duration_ms = int((time.monotonic() - started) * 1000)
    await _record_tool_execution(
        session,
        agent_run_id=agent_run_id,
        sequence=sequence,
        tool_name=f"{kind}.fetch_jobs",
        status="succeeded",
        duration_ms=duration_ms,
        arguments={"external_key": external_key},
        result={
            "jobs_seen": len(postings),
            "created": created,
            "updated": updated,
            "closed": closed,
        },
    )
    await session.flush()

    await events_service.emit(
        user_id=user_id,
        kind="discovery_source_finished",
        message=(
            f"{kind}:{external_key} — {len(postings)} posting(s): "
            f"{created} new, {updated} updated, {closed} closed."
        ),
        agent_run_id=agent_run_id,
        data={"source": external_key, "created": created, "updated": updated, "closed": closed},
    )

    return SourceDiscoveryResult(
        kind=kind,
        external_key=external_key,
        status="ok",
        jobs_seen=len(postings),
        jobs_created=created,
        jobs_updated=updated,
        jobs_closed=closed,
        duration_ms=duration_ms,
        retry_count=retry_count,
    )


async def _close_missing_jobs(
    session: AsyncSession, *, job_source_id: uuid.UUID, seen_source_job_ids: set[str]
) -> int:
    """Mark previously-open jobs from this source as closed if they weren't just seen.

    Scoped tightly: only this ``job_source_id``, only rows currently ``OPEN``, never
    demo data (a real connector never writes ``is_demo_data=True`` in the first
    place, but the filter is explicit here too, as a second line of defence). Rows
    are updated, never deleted — the posting's history stays queryable.
    """
    result = await session.execute(
        select(Job).where(
            Job.job_source_id == job_source_id,
            Job.source_status == SourceStatus.OPEN,
            Job.is_demo_data.is_(False),
        )
    )
    closed = 0
    for job in result.scalars().all():
        if job.source_job_id not in seen_source_job_ids:
            job.source_status = SourceStatus.CLOSED
            closed += 1
    if closed:
        await session.flush()
    return closed


async def _record_tool_execution(
    session: AsyncSession,
    *,
    agent_run_id: uuid.UUID,
    sequence: int,
    tool_name: str,
    status: str,
    duration_ms: int,
    arguments: dict[str, object],
    result: dict[str, object] | None = None,
    error: str | None = None,
) -> None:
    session.add(
        ToolExecution(
            agent_run_id=agent_run_id,
            sequence=sequence,
            tool_name=tool_name,
            status=status,
            duration_ms=duration_ms,
            # Board tokens and counts only — never raw page content.
            arguments_redacted=arguments,
            result_redacted=result or {},
            error=error,
        )
    )
