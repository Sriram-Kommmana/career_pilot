"""Job feed, job detail, and analysis routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.api.deps import AppSettings, CurrentUserId, DbSession
from app.core.errors import NotImplementedPhaseError
from app.models import Job, JobMatchAssessment
from app.schemas.common import Page
from app.schemas.job import (
    DiscoverRequest,
    JobDetailOut,
    JobFeedItemOut,
    JobListQuery,
    JobSummaryOut,
    MatchAssessmentOut,
)
from app.services import jobs as jobs_service
from app.services import matching as matching_service
from app.services.jobs import JobFilters

router = APIRouter(prefix="/jobs", tags=["jobs"])

JobId = Annotated[uuid.UUID, Path(description="Job id.")]


def _job_summary(job: Job, *, source_count: int = 1) -> JobSummaryOut:
    """Build the summary payload, flattening the source and salary.

    Done explicitly rather than by ORM attribute mapping so ``source`` reads as the
    connector kind and a job with no salary gets ``None`` rather than an empty object.
    """
    from app.schemas.job import CompanyOut, SalaryOut

    salary = None
    if job.salary_min is not None or job.salary_max is not None:
        salary = SalaryOut(
            minimum=float(job.salary_min) if job.salary_min is not None else None,
            maximum=float(job.salary_max) if job.salary_max is not None else None,
            currency=job.salary_currency,
            period=job.salary_period,
        )
    return JobSummaryOut(
        id=job.id,
        title=job.title,
        company=CompanyOut.model_validate(job.company),
        location_raw=job.location_raw,
        work_arrangement=job.work_arrangement,
        employment_type=job.employment_type,
        application_url=job.application_url,
        source=job.job_source.kind if job.job_source else "unknown",
        source_status=job.source_status,
        posted_at=job.posted_at,
        posted_at_precision=job.posted_at_precision,
        last_observed_at=job.last_observed_at,
        is_demo_data=job.is_demo_data,
        salary=salary,
        source_count=source_count,
    )


def _assessment_out(row: JobMatchAssessment) -> MatchAssessmentOut:
    return MatchAssessmentOut.model_validate(row)


@router.get("", response_model=Page[JobFeedItemOut])
async def list_jobs(
    session: DbSession,
    user_id: CurrentUserId,
    settings: AppSettings,
    query: Annotated[JobListQuery, Depends()],
) -> Page[JobFeedItemOut]:
    """A page of jobs with this user's stored match assessment for each.

    Assessments are computed on demand for jobs that do not have one, so the feed is
    useful immediately after the profile changes without a background sweep.

    ``min_score`` filters on the computed score. Jobs that could not be scored are
    excluded when it is set, because their rank is unknown rather than low.
    """
    filters = JobFilters(
        search=query.search,
        company=query.company,
        location=query.location,
        work_arrangement=query.work_arrangement,
        employment_type=query.employment_type,
        source=query.source,
        is_demo_data=None if query.include_demo else False,
        exclude_closed=query.exclude_closed,
    )
    rows, total = await jobs_service.list_jobs(
        session, filters=filters, limit=query.limit, offset=query.offset
    )
    assessments = await matching_service.analyze_many(
        session, user_id=user_id, jobs=rows, settings=settings
    )
    duplicate_counts = await jobs_service.count_duplicates(
        session, [job.fingerprint for job in rows]
    )

    items: list[JobFeedItemOut] = []
    for job in rows:
        assessment = assessments.get(job.id)
        # A job that could not be scored is excluded when a minimum is set: its rank
        # is unknown, which is not the same as being below the bar.
        if query.min_score is not None and (
            assessment is None or assessment.score is None or assessment.score < query.min_score
        ):
            continue
        items.append(
            JobFeedItemOut(
                job=_job_summary(job, source_count=duplicate_counts.get(job.fingerprint, 1)),
                match=_assessment_out(assessment) if assessment is not None else None,
            )
        )

    return Page[JobFeedItemOut](items=items, total=total, limit=query.limit, offset=query.offset)


@router.get("/{job_id}", response_model=JobDetailOut)
async def get_job(job_id: JobId, session: DbSession, user_id: CurrentUserId) -> JobDetailOut:
    """One job, its requirements, the stored assessment, and cross-source duplicates."""
    from app.schemas.job import JobRequirementOut

    job = await jobs_service.get_job(session, job_id)
    assessment = await matching_service.get_stored_assessment(
        session, user_id=user_id, job_id=job_id
    )
    duplicates = await jobs_service.find_duplicates(session, job)

    return JobDetailOut(
        job=_job_summary(job, source_count=len(duplicates) + 1),
        description=job.description,
        requirements=[JobRequirementOut.model_validate(r) for r in job.requirements],
        responsibilities=list(job.responsibilities),
        match=_assessment_out(assessment) if assessment is not None else None,
        duplicates=[_job_summary(d) for d in duplicates],
    )


@router.post("/{job_id}/analyze", response_model=MatchAssessmentOut)
async def analyze_job(
    job_id: JobId,
    session: DbSession,
    user_id: CurrentUserId,
    settings: AppSettings,
) -> MatchAssessmentOut:
    """Re-run the deterministic match engine for this job and store the result.

    No model is involved, so the same profile and posting always produce the same
    score and the same explanation.
    """
    job = await jobs_service.get_job(session, job_id)
    row, _ = await matching_service.analyze_and_store(
        session, user_id=user_id, job=job, settings=settings
    )
    return _assessment_out(row)


@router.post("/discover", status_code=202)
async def discover_jobs(payload: DiscoverRequest, user_id: CurrentUserId) -> dict[str, object]:
    """Trigger a connector run.

    Not implemented in Phase 1: there are no live connectors yet, so this returns 501
    rather than pretending to have searched anything. Greenhouse, Lever, Ashby and
    configurable career pages arrive in Phase 2.
    """
    raise NotImplementedPhaseError("Live job discovery", phase=2)
