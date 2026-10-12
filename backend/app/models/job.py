"""Companies, job sources, jobs, and match assessments.

The two uniqueness rules here are the whole deduplication strategy:

* ``UNIQUE (job_source_id, source_job_id)`` — re-running a connector updates rows
  rather than inserting copies. This is enforced by the database, not by a check in
  application code that a concurrent worker could race.
* ``fingerprint`` — a non-unique, indexed hash used to *link* the same role listed on
  more than one source. Deliberately not a constraint: it is a heuristic, and a
  heuristic must never be able to destroy a row.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column
from app.domain.matching import ScoreKind
from app.domain.normalize import (
    EmploymentType,
    PostedAtPrecision,
    SourceStatus,
    WorkArrangement,
)


class Company(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    #: Normalised comparison key from ``domain.normalize.normalize_company_name``.
    canonical_name: Mapped[str] = mapped_column(
        sa.String(300), nullable=False, unique=True, index=True
    )
    website: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    careers_url: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    jobs: Mapped[list[Job]] = relationship(back_populates="company")


class JobSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A configured place jobs come from.

    ``kind`` names the connector ("greenhouse", "lever", "ashby", "career_page",
    "demo_seed"). ``config`` holds that connector's parameters, e.g. a board token.

    The health columns exist so a connector that quietly stops returning results is
    visible instead of looking like an empty job market. A source that keeps failing
    is reported in the UI rather than retried forever.
    """

    __tablename__ = "job_sources"

    kind: Mapped[str] = mapped_column(sa.String(40), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    #: Stable identifier within the kind, e.g. a Greenhouse board token.
    external_key: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    config: Mapped[dict[str, object]] = mapped_column(nullable=False, default=dict)
    is_enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)

    last_run_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    jobs: Mapped[list[Job]] = relationship(back_populates="job_source")

    __table_args__ = (sa.UniqueConstraint("kind", "external_key", name="kind_external_key"),)


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A job posting, normalised.

    Three columns are about honesty rather than data modelling:

    ``posted_at`` / ``posted_at_precision`` — a check constraint makes it impossible to
    store a date without saying how precise it is, or a precision without a date. The
    UI renders ``UNKNOWN`` as "posting date not provided", never as "posted recently".

    ``is_demo_data`` — seeded sample jobs are flagged in the database itself, carried
    through the API, and badged in the UI. There is no way to accidentally present a
    seeded job as a live one.

    ``source_status`` — what the *source* said, not our inference. ``UNKNOWN`` is the
    default, because most feeds say nothing.
    """

    __tablename__ = "jobs"

    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    job_source_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("job_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_job_id: Mapped[str] = mapped_column(sa.String(300), nullable=False)

    title: Mapped[str] = mapped_column(sa.String(400), nullable=False)
    canonical_title: Mapped[str] = mapped_column(sa.String(400), nullable=False, index=True)
    location_raw: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    canonical_location: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)

    work_arrangement: Mapped[WorkArrangement] = mapped_column(
        enum_column(WorkArrangement, name="job_work_arrangement"),
        nullable=False,
        default=WorkArrangement.UNKNOWN,
    )
    employment_type: Mapped[EmploymentType] = mapped_column(
        enum_column(EmploymentType, name="job_employment_type"),
        nullable=False,
        default=EmploymentType.UNKNOWN,
    )

    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    responsibilities: Mapped[list[str]] = mapped_column(nullable=False, default=list)

    salary_min: Mapped[float | None] = mapped_column(sa.Numeric(14, 2), nullable=True)
    salary_max: Mapped[float | None] = mapped_column(sa.Numeric(14, 2), nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(sa.String(3), nullable=True)
    salary_period: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)

    posted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    posted_at_precision: Mapped[PostedAtPrecision] = mapped_column(
        enum_column(PostedAtPrecision, name="job_posted_at_precision"),
        nullable=False,
        default=PostedAtPrecision.UNKNOWN,
    )

    application_url: Mapped[str] = mapped_column(sa.String(1000), nullable=False)
    source_status: Mapped[SourceStatus] = mapped_column(
        enum_column(SourceStatus, name="job_source_status"),
        nullable=False,
        default=SourceStatus.UNKNOWN,
    )
    first_seen_at: Mapped[datetime] = mapped_column(server_default=sa.func.now(), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(
        server_default=sa.func.now(), nullable=False, index=True
    )

    #: Cross-source identity. Indexed but intentionally not unique; see module docstring.
    fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)

    #: True for rows created by ``app.seed``. Never true for connector output.
    is_demo_data: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, index=True
    )

    company: Mapped[Company] = relationship(back_populates="jobs")
    job_source: Mapped[JobSource] = relationship(back_populates="jobs")
    requirements: Mapped[list[JobRequirement]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobRequirement.position"
    )

    __table_args__ = (
        sa.UniqueConstraint("job_source_id", "source_job_id", name="job_source_id_source_job_id"),
        sa.CheckConstraint(
            "(posted_at IS NULL AND posted_at_precision = 'UNKNOWN') "
            "OR (posted_at IS NOT NULL AND posted_at_precision <> 'UNKNOWN')",
            name="posted_at_precision_consistent",
        ),
        sa.CheckConstraint(
            "salary_min IS NULL OR salary_max IS NULL OR salary_max >= salary_min",
            name="salary_range_ordered",
        ),
        sa.Index("ix_jobs_feed", "last_observed_at", "is_demo_data"),
    )

    @property
    def has_known_posting_date(self) -> bool:
        return self.posted_at_precision is not PostedAtPrecision.UNKNOWN


class JobRequirement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One requirement line from a posting.

    Stored as individual rows rather than a blob so a match assessment can point at
    the exact line it judged, and the UI can show met/missing per line.
    """

    __tablename__ = "job_requirements"

    job_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    #: True when the wording marks it "preferred" or "nice to have".
    is_optional: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    #: Canonical skill names recognised in this line, for querying.
    extracted_skills: Mapped[list[str]] = mapped_column(nullable=False, default=list)

    job: Mapped[Job] = relationship(back_populates="requirements")

    __table_args__ = (sa.UniqueConstraint("job_id", "position", name="job_id_position"),)


class JobMatchAssessment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A stored match assessment for one (user, job) pair.

    Everything needed to re-render the explanation is persisted: the component scores,
    the weights actually used, the per-requirement verdicts, and the evidence
    references. A stored assessment is therefore auditable after the fact, and a
    change in the engine does not silently rewrite history — ``engine_version`` says
    which version produced it.

    ``score`` is nullable. ``NULL`` means "not enough information to rank", which is
    different from a score of zero, and the API preserves that distinction.
    """

    __tablename__ = "job_match_assessments"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    score: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    score_kind: Mapped[ScoreKind] = mapped_column(
        enum_column(ScoreKind, name="match_score_kind"),
        nullable=False,
        default=ScoreKind.RANKING_HEURISTIC,
    )
    #: False when a hard filter excluded the job. Such rows keep ``score = NULL``.
    passed_hard_filters: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    hard_filter_failures: Mapped[list[dict[str, object]]] = mapped_column(
        nullable=False, default=list
    )

    components: Mapped[list[dict[str, object]]] = mapped_column(nullable=False, default=list)
    weights: Mapped[dict[str, float]] = mapped_column(nullable=False, default=dict)
    assessed_weight: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)

    requirement_results: Mapped[list[dict[str, object]]] = mapped_column(
        nullable=False, default=list
    )
    explanation: Mapped[dict[str, object]] = mapped_column(nullable=False, default=dict)
    evidence_refs: Mapped[list[dict[str, object]]] = mapped_column(nullable=False, default=list)

    engine_version: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    assessed_at: Mapped[datetime] = mapped_column(server_default=sa.func.now(), nullable=False)

    job: Mapped[Job] = relationship()

    __table_args__ = (
        # One current assessment per pair; re-analysing updates it in place.
        sa.UniqueConstraint("user_id", "job_id", name="user_id_job_id"),
        sa.CheckConstraint("score IS NULL OR (score >= 0 AND score <= 1)", name="score_range"),
        sa.CheckConstraint(
            "passed_hard_filters OR score IS NULL",
            name="filtered_jobs_are_unscored",
        ),
        sa.Index("ix_match_user_score", "user_id", sa.text("score DESC NULLS LAST")),
    )
