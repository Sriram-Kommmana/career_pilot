"""Running the match engine and persisting the result.

This module is a thin shell on purpose. The scoring itself is in
``app.domain.matching``, which has no database and no clock, so this layer only:

1. loads the profile and the job,
2. converts them to the pure domain inputs,
3. calls :func:`~app.domain.matching.assess_match`,
4. serialises the whole assessment into one row.

Everything needed to re-render an explanation is stored, so the UI never recomputes a
score to display one and a later change to the engine does not silently rewrite what a
user was previously shown.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings, get_settings
from app.domain.matching import (
    JobPosting as DomainJob,
)
from app.domain.matching import (
    MatchAssessment,
    MatchConfig,
    assess_match,
)
from app.domain.normalize import SalaryRange
from app.models import Job, JobMatchAssessment, Profile
from app.services.profile import build_candidate_profile, get_profile


def to_domain_job(job: Job) -> DomainJob:
    """Convert a stored posting into the match engine's input type."""
    salary: SalaryRange | None = None
    if job.salary_min is not None or job.salary_max is not None:
        salary = SalaryRange(
            minimum=float(job.salary_min) if job.salary_min is not None else None,
            maximum=float(job.salary_max) if job.salary_max is not None else None,
            currency=job.salary_currency,
            period=job.salary_period,
        )
    return DomainJob(
        title=job.title,
        company_name=job.company.name if job.company else "",
        location_raw=job.location_raw,
        work_arrangement=job.work_arrangement,
        employment_type=job.employment_type,
        requirement_texts=tuple(r.text for r in job.requirements),
        description=job.description,
        salary=salary,
        source_status=job.source_status,
    )


def config_from_settings(settings: Settings | None = None) -> MatchConfig:
    cfg = settings or get_settings()
    return MatchConfig(weights=cfg.weights.as_mapping())


def assess(
    *,
    profile: Profile,
    job: Job,
    settings: Settings | None = None,
) -> MatchAssessment:
    """Score one job for one profile. No I/O."""
    return assess_match(
        profile=build_candidate_profile(profile),
        job=to_domain_job(job),
        config=config_from_settings(settings),
    )


def serialise_assessment(assessment: MatchAssessment) -> dict[str, object]:
    """Flatten an assessment into the JSONB payloads the row stores."""
    return {
        "score": assessment.score,
        "score_kind": assessment.score_kind,
        "passed_hard_filters": assessment.hard_filter.passed,
        "hard_filter_failures": [
            {"code": f.code.value, "message": f.message, "detail": f.detail}
            for f in assessment.hard_filter.failures
        ],
        "components": [
            {
                "name": c.name,
                "raw": c.raw,
                "weight": c.weight,
                "weighted": c.weighted,
                "assessed": c.assessed,
                "rationale": c.rationale,
                "details": c.details,
            }
            for c in assessment.components
        ],
        "requirement_results": [
            {
                "text": r.text,
                "status": r.status.value,
                "is_optional": r.is_optional,
                "matched_skills": list(r.matched_skills),
                "missing_skills": list(r.missing_skills),
                "evidence": [ref.describe() for ref in r.evidence],
                "note": r.note,
            }
            for r in assessment.requirements
        ],
        "explanation": {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in asdict(assessment.explanation).items()
        },
        "evidence_refs": [
            {
                "kind": ref.kind,
                "id": str(ref.id) if ref.id else None,
                "locator": ref.locator,
            }
            for ref in assessment.evidence_refs
        ],
        "assessed_weight": assessment.assessed_weight,
        "engine_version": assessment.engine_version,
    }


async def analyze_and_store(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    job: Job,
    settings: Settings | None = None,
) -> tuple[JobMatchAssessment, MatchAssessment]:
    """Assess a job and upsert the stored assessment for this (user, job) pair."""
    profile = await get_profile(session, user_id=user_id)
    assessment = assess(profile=profile, job=job, settings=settings)
    payload = serialise_assessment(assessment)

    result = await session.execute(
        select(JobMatchAssessment).where(
            JobMatchAssessment.user_id == user_id,
            JobMatchAssessment.job_id == job.id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = JobMatchAssessment(user_id=user_id, job_id=job.id)
        session.add(row)

    for key, value in payload.items():
        setattr(row, key, value)
    row.assessed_at = utcnow()

    await session.flush()
    return row, assessment


async def get_stored_assessment(
    session: AsyncSession, *, user_id: uuid.UUID, job_id: uuid.UUID
) -> JobMatchAssessment | None:
    result = await session.execute(
        select(JobMatchAssessment).where(
            JobMatchAssessment.user_id == user_id,
            JobMatchAssessment.job_id == job_id,
        )
    )
    return result.scalar_one_or_none()


async def analyze_many(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    jobs: list[Job],
    settings: Settings | None = None,
) -> dict[uuid.UUID, JobMatchAssessment]:
    """Assess a batch of jobs, e.g. after loading a page of the feed.

    The profile is loaded once rather than per job, which matters because the
    conversion walks every project and experience record.
    """
    if not jobs:
        return {}

    profile = await get_profile(session, user_id=user_id)
    candidate = build_candidate_profile(profile)
    config = config_from_settings(settings)

    existing_rows = await session.execute(
        select(JobMatchAssessment).where(
            JobMatchAssessment.user_id == user_id,
            JobMatchAssessment.job_id.in_([job.id for job in jobs]),
        )
    )
    by_job: dict[uuid.UUID, JobMatchAssessment] = {
        row.job_id: row for row in existing_rows.scalars().all()
    }

    now = utcnow()
    for job in jobs:
        assessment = assess_match(profile=candidate, job=to_domain_job(job), config=config)
        row = by_job.get(job.id)
        if row is None:
            row = JobMatchAssessment(user_id=user_id, job_id=job.id)
            session.add(row)
            by_job[job.id] = row
        for key, value in serialise_assessment(assessment).items():
            setattr(row, key, value)
        row.assessed_at = now

    await session.flush()
    return by_job
