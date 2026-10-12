"""Unattended scheduled work: the notification sweep behind ``python -m app.cli sweep``.

This module is pure orchestration over existing services — it adds no new business
logic. ``app.services.matching`` decides scores, ``app.services.notifications``
decides what counts as a notify-worthy duplicate, and this module only decides *which*
users and jobs to run them over, then records what happened.

Phase 4A stops at telling a user about a match. It never creates or advances an
``Application`` row — that is Phase 4B's "personalized application preparation"
milestone, kept deliberately separate from this one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.orm import selectinload

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.logging import get_correlation_id, get_logger
from app.domain.normalize import SourceStatus
from app.domain.redaction import redact_text
from app.models import AgentRun, Job, Profile
from app.services import matching as matching_service
from app.services import notifications as notifications_service
from app.services.profile import get_profile

logger = get_logger(__name__)

#: Arbitrary, fixed key for a Postgres session-level advisory lock. Its only purpose
#: is "at most one sweep at a time" — every write the sweep performs is independently
#: idempotent (see app/services/scheduler.py callers), so this is defence against
#: wasted duplicate work and noisy audit rows under an overlapping scheduler trigger,
#: not a correctness requirement.
SWEEP_ADVISORY_LOCK_KEY: Final[int] = 0x43505357  # ASCII "CPSW" ("CareerPilot SWeep")


async def try_acquire_sweep_lock(connection: AsyncConnection) -> bool:
    result = await connection.execute(
        sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": SWEEP_ADVISORY_LOCK_KEY}
    )
    return bool(result.scalar_one())


async def release_sweep_lock(connection: AsyncConnection) -> None:
    await connection.execute(
        sa.text("SELECT pg_advisory_unlock(:key)"), {"key": SWEEP_ADVISORY_LOCK_KEY}
    )


@dataclass(frozen=True, slots=True)
class NotificationSweepResult:
    """What one notification-sweep run did, for the CLI summary and the audit row."""

    agent_run_id: uuid.UUID
    users_considered: int = 0
    users_notified: int = 0
    users_failed: int = 0
    jobs_scored: int = 0
    notifications_created: int = 0


async def _open_non_demo_jobs(session: AsyncSession) -> list[Job]:
    """Jobs eligible to be matched and notified about.

    Demo data is excluded deliberately: ``docs/SAFETY.md`` section 12 and
    ``CLAUDE.md`` require seeded rows to never be presented as live postings, and a
    real notification is exactly that presentation.
    """
    result = await session.execute(
        select(Job)
        .where(Job.source_status == SourceStatus.OPEN, Job.is_demo_data.is_(False))
        .options(selectinload(Job.company), selectinload(Job.requirements))
    )
    return list(result.scalars().all())


async def run_notification_sweep(
    session: AsyncSession, *, settings: Settings
) -> NotificationSweepResult:
    """Score every open, non-demo job against every user and notify on threshold.

    One user's failure is caught and logged, never allowed to abort the run for
    everyone else — the same partial-failure discipline ``discovery.run_discovery``
    already applies per job source.
    """
    agent_run = AgentRun(
        user_id=None,
        kind="notify_sweep",
        status="running",
        correlation_id=get_correlation_id(),
        context={},
    )
    session.add(agent_run)
    await session.flush()

    jobs = await _open_non_demo_jobs(session)
    user_ids = list((await session.execute(select(Profile.user_id))).scalars().all())

    users_notified = 0
    users_failed = 0
    notifications_created = 0

    for user_id in user_ids:
        try:
            # Each user's work runs inside its own SAVEPOINT, scoped around every
            # read *and* write for that user (profile load, scoring, notifying).
            # If anything raises partway through — e.g. analyze_many fails on the
            # second of several jobs after already session.add()-ing a
            # JobMatchAssessment for the first — rolling back to this SAVEPOINT
            # discards that partial, never-flushed work along with it. Without
            # this, the added-but-unflushed row stays pending in the session and
            # would get silently flushed later by the *next* user's notification
            # insert (session.flush() flushes the whole unit of work, not just
            # the caller's own object) — exactly the defect this guards against.
            # A plain session.rollback() here would be wrong for the opposite
            # reason: it discards every other user already processed this sweep,
            # not just this one's.
            async with session.begin_nested():
                profile = await get_profile(session, user_id=user_id)
                assessments = await matching_service.analyze_many(
                    session, user_id=user_id, jobs=jobs, settings=settings
                )
                matches = [
                    (
                        job.id,
                        job.title,
                        job.company.name if job.company else "",
                        assessments[job.id].score,
                    )
                    for job in jobs
                ]
                created = await notifications_service.notify_job_matches(
                    session,
                    user_id=user_id,
                    matches=matches,
                    threshold=profile.notify_score_threshold,
                )
            notifications_created += created
            if created:
                users_notified += 1
        except Exception as exc:
            users_failed += 1
            logger.warning(
                "sweep_user_failed", user_id=str(user_id), error=redact_text(str(exc))
            )
            continue

    agent_run.status = "succeeded"
    agent_run.finished_at = utcnow()
    agent_run.duration_ms = int(
        (agent_run.finished_at - agent_run.started_at).total_seconds() * 1000
    )
    agent_run.summary = (
        f"Scored {len(jobs)} open job(s) against {len(user_ids)} user(s): "
        f"{notifications_created} notification(s) created, {users_notified} user(s) "
        f"notified, {users_failed} user(s) failed."
    )
    await session.flush()

    return NotificationSweepResult(
        agent_run_id=agent_run.id,
        users_considered=len(user_ids),
        users_notified=users_notified,
        users_failed=users_failed,
        jobs_scored=len(jobs),
        notifications_created=notifications_created,
    )
