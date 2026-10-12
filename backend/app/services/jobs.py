"""Job reads and ingestion.

:func:`upsert_job` is the single write path for a posting, used by the seeder now and
by every connector from Phase 2. It keys on ``(job_source_id, source_job_id)``, so
re-running a source updates rows rather than duplicating them, and it never
fabricates a posting date: a source that omits one leaves ``posted_at`` NULL with
precision ``UNKNOWN``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Select, delete, func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.clock import utcnow
from app.core.errors import NotFoundError
from app.domain.dedup import (
    default_source_priority,
    job_fingerprint,
    source_priorities,
)
from app.domain.normalize import (
    EmploymentType,
    NormalizedJob,
    SourceStatus,
    WorkArrangement,
    normalize_company_name,
    normalize_job_title,
    normalize_location,
)
from app.domain.skills import canonical_skill_set, is_optional_requirement
from app.models import Company, Job, JobRequirement, JobSource


@dataclass(frozen=True, slots=True)
class JobFilters:
    """Server-side filters for the job feed. All optional."""

    search: str | None = None
    company: str | None = None
    location: str | None = None
    work_arrangement: WorkArrangement | None = None
    employment_type: EmploymentType | None = None
    source: str | None = None
    #: None shows everything; False hides seeded demo rows; True shows only them.
    is_demo_data: bool | None = None
    #: Hide postings the source has marked closed.
    exclude_closed: bool = False
    min_score: float | None = None
    #: Show one row per logical job when the same role appears on several sources.
    #: The others are kept in storage and listed on the job detail page.
    collapse_duplicates: bool = True


def _source_priority_expression() -> sa.Case[int]:
    """A SQL CASE mirroring ``domain.dedup`` source preference.

    Built from the domain's table rather than hardcoded, so the feed's choice of
    canonical row and ``group_duplicates`` cannot disagree.
    """
    return sa.case(
        *[
            (JobSource.kind == kind, priority)
            for kind, priority in sorted(source_priorities().items())
        ],
        else_=default_source_priority(),
    )


def _apply_filters[StatementT: Select[Any]](
    statement: StatementT, filters: JobFilters
) -> StatementT:
    if filters.search:
        pattern = f"%{filters.search.strip()}%"
        statement = statement.where(Job.title.ilike(pattern) | Job.description.ilike(pattern))
    if filters.company:
        statement = statement.where(
            Company.canonical_name == normalize_company_name(filters.company)
        )
    if filters.location:
        needle = normalize_location(filters.location)
        if needle:
            statement = statement.where(Job.canonical_location.ilike(f"%{needle}%"))
    if filters.work_arrangement is not None:
        statement = statement.where(Job.work_arrangement == filters.work_arrangement)
    if filters.employment_type is not None:
        statement = statement.where(Job.employment_type == filters.employment_type)
    if filters.source:
        statement = statement.where(JobSource.kind == filters.source)
    if filters.is_demo_data is not None:
        statement = statement.where(Job.is_demo_data.is_(filters.is_demo_data))
    if filters.exclude_closed:
        statement = statement.where(Job.source_status != SourceStatus.CLOSED)
    return statement


async def list_jobs(
    session: AsyncSession,
    *,
    filters: JobFilters | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Job], int]:
    """A page of jobs plus the total matching count."""
    active_filters = filters or JobFilters()

    # Select the matching ids first. Collapsing duplicates has to happen before
    # LIMIT/OFFSET: filtering them out of an already-paginated page would produce
    # pages of varying size and could skip rows at a page boundary.
    id_query = (
        select(Job.id)
        .join(Company, Job.company_id == Company.id)
        .join(JobSource, Job.job_source_id == JobSource.id)
    )
    id_query = _apply_filters(id_query, active_filters)

    if active_filters.collapse_duplicates:
        # One row per fingerprint, preferring the source the employer actually uses.
        # DISTINCT ON requires the distinct column to lead the ORDER BY. The
        # positional-argument form of .distinct() is deprecated as of SQLAlchemy
        # 2.1 in favour of the postgresql.distinct_on() extension construct.
        id_query = id_query.ext(distinct_on(Job.fingerprint)).order_by(
            Job.fingerprint,
            _source_priority_expression(),
            Job.last_observed_at.desc(),
        )

    id_subquery = id_query.subquery()
    total = (await session.execute(select(func.count()).select_from(id_subquery))).scalar_one()

    page_statement = (
        select(Job)
        .where(Job.id.in_(select(id_subquery.c.id)))
        .options(
            selectinload(Job.company),
            selectinload(Job.job_source),
            selectinload(Job.requirements),
        )
        # Newest observation first; a job with no posting date still sorts sensibly
        # because last_observed_at is always set.
        .order_by(Job.last_observed_at.desc(), Job.title.asc())
        .limit(limit)
        .offset(offset)
    )
    rows = list((await session.execute(page_statement)).scalars().all())
    return rows, total


async def count_duplicates(session: AsyncSession, fingerprints: list[str]) -> dict[str, int]:
    """How many stored rows share each fingerprint.

    Used to tell the user a role is also listed elsewhere, without loading the other
    rows into the feed response.
    """
    if not fingerprints:
        return {}
    result = await session.execute(
        select(Job.fingerprint, func.count())
        .where(Job.fingerprint.in_(fingerprints))
        .group_by(Job.fingerprint)
    )
    return {row[0]: row[1] for row in result.all()}


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job:
    result = await session.execute(
        select(Job)
        .where(Job.id == job_id)
        .options(
            selectinload(Job.company),
            selectinload(Job.job_source),
            selectinload(Job.requirements),
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise NotFoundError("Job not found.")
    return job


async def find_duplicates(session: AsyncSession, job: Job) -> list[Job]:
    """Other stored postings that look like the same role.

    Used on the job detail page to show alternative places to apply. These rows are
    never deleted; cross-source identity is a heuristic, and a heuristic must not be
    able to destroy data.
    """
    result = await session.execute(
        select(Job)
        .where(Job.fingerprint == job.fingerprint, Job.id != job.id)
        .options(selectinload(Job.company), selectinload(Job.job_source))
    )
    return list(result.scalars().all())


async def get_or_create_company(session: AsyncSession, name: str) -> Company:
    canonical = normalize_company_name(name)
    result = await session.execute(select(Company).where(Company.canonical_name == canonical))
    company = result.scalar_one_or_none()
    if company is None:
        company = Company(name=name.strip(), canonical_name=canonical)
        session.add(company)
        await session.flush()
    return company


async def get_or_create_source(
    session: AsyncSession, *, kind: str, external_key: str, name: str | None = None
) -> JobSource:
    result = await session.execute(
        select(JobSource).where(JobSource.kind == kind, JobSource.external_key == external_key)
    )
    source = result.scalar_one_or_none()
    if source is None:
        source = JobSource(
            kind=kind,
            external_key=external_key,
            name=name or f"{kind}:{external_key}",
        )
        session.add(source)
        await session.flush()
    return source


async def upsert_job(
    session: AsyncSession,
    *,
    normalized: NormalizedJob,
    source_external_key: str,
    is_demo_data: bool = False,
) -> tuple[Job, bool]:
    """Insert or update one posting. Returns ``(job, created)``.

    Keyed on ``(job_source_id, source_job_id)``, which is also a database uniqueness
    constraint, so a concurrent connector run cannot produce a duplicate.
    """
    company = await get_or_create_company(session, normalized.company_name)
    source = await get_or_create_source(
        session, kind=normalized.source, external_key=source_external_key
    )

    result = await session.execute(
        select(Job)
        .where(
            Job.job_source_id == source.id,
            Job.source_job_id == normalized.source_job_id,
        )
        .options(selectinload(Job.requirements))
    )
    job = result.scalar_one_or_none()
    created = job is None
    observed_at = normalized.last_observed_at or utcnow()

    if job is None:
        job = Job(
            company_id=company.id,
            job_source_id=source.id,
            source_job_id=normalized.source_job_id,
            first_seen_at=observed_at,
            is_demo_data=is_demo_data,
        )
        session.add(job)

    job.title = normalized.title
    job.canonical_title = normalize_job_title(normalized.title)
    job.location_raw = normalized.location_raw
    job.canonical_location = normalize_location(normalized.location_raw) or None
    job.work_arrangement = normalized.work_arrangement
    job.employment_type = normalized.employment_type
    job.description = normalized.description
    job.responsibilities = list(normalized.responsibilities)
    job.application_url = normalized.application_url
    job.source_status = normalized.source_status
    job.last_observed_at = observed_at
    # Never substituted: a posting with no date keeps NULL and precision UNKNOWN.
    job.posted_at = normalized.posted_at
    job.posted_at_precision = normalized.posted_at_precision
    job.fingerprint = job_fingerprint(
        company_name=normalized.company_name,
        title=normalized.title,
        location=normalized.location_raw,
    )

    if normalized.salary is not None:
        job.salary_min = normalized.salary.minimum
        job.salary_max = normalized.salary.maximum
        job.salary_currency = normalized.salary.currency
        job.salary_period = normalized.salary.period

    await session.flush()
    await _replace_requirements(session, job=job, texts=normalized.requirements)
    return job, created


async def _replace_requirements(session: AsyncSession, *, job: Job, texts: tuple[str, ...]) -> None:
    """Replace a job's requirement rows with the current set.

    Replaced wholesale rather than diffed: requirement lines have no stable identity
    across fetches, so matching them up would be guesswork.

    The delete is issued as a statement rather than by iterating
    ``job.requirements``. Reading that collection on a newly inserted job would
    trigger a lazy load, which under the asyncio driver raises ``MissingGreenlet``
    instead of quietly emitting a query.
    """
    await session.execute(delete(JobRequirement).where(JobRequirement.job_id == job.id))
    # Expire the stale collection so a later read reloads it rather than returning the
    # rows we just deleted.
    session.expire(job, ["requirements"])
    await session.flush()

    for position, text in enumerate(texts):
        cleaned = text.strip()
        if not cleaned:
            continue
        session.add(
            JobRequirement(
                job_id=job.id,
                position=position,
                text=cleaned,
                is_optional=is_optional_requirement(cleaned),
                extracted_skills=sorted(canonical_skill_set(cleaned.split(","))),
            )
        )
    await session.flush()
