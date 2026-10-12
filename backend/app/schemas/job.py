"""Job and match-assessment contracts.

Two fields exist purely so the client cannot misrepresent the data:

``posted_at_precision`` travels with ``posted_at``. When it is ``UNKNOWN`` the UI must
render "posting date not provided", never "posted recently". The API refuses to
guess, so the client has nothing to guess from.

``score_kind`` is always ``RANKING_HEURISTIC``. It is in the payload so that any
client reading a score has the label for what the number is right beside it: an
ordering heuristic, not a probability of an interview or an offer.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from app.domain.matching import ScoreKind
from app.domain.normalize import (
    EmploymentType,
    PostedAtPrecision,
    SourceStatus,
    WorkArrangement,
)
from app.schemas.common import ApiModel, ApiRequest


class CompanyOut(ApiModel):
    id: uuid.UUID
    name: str
    website: str | None = None
    careers_url: str | None = None


class JobRequirementOut(ApiModel):
    id: uuid.UUID
    position: int
    text: str
    is_optional: bool
    extracted_skills: list[str]


class SalaryOut(ApiModel):
    minimum: float | None = None
    maximum: float | None = None
    currency: str | None = None
    period: str | None = None


class JobSummaryOut(ApiModel):
    id: uuid.UUID
    title: str
    company: CompanyOut
    location_raw: str | None
    work_arrangement: WorkArrangement
    employment_type: EmploymentType
    application_url: str
    source: str = Field(description="The connector that produced this row.")
    source_status: SourceStatus

    posted_at: datetime | None
    posted_at_precision: PostedAtPrecision
    last_observed_at: datetime

    #: True for seeded sample rows. The UI shows a visible badge for these; they are
    #: never presented as live postings.
    is_demo_data: bool
    salary: SalaryOut | None = None
    #: How many stored rows describe this same role, across all sources. 1 means only
    #: this one. Duplicates are linked, never deleted, so each keeps its own
    #: application URL.
    source_count: int = 1


class MatchComponentOut(ApiModel):
    name: str
    #: ``None`` means the component could not be assessed, which is not the same as
    #: scoring zero. Such a component is excluded and the remaining weights are
    #: renormalised.
    raw: float | None
    weight: float
    weighted: float
    assessed: bool
    rationale: str
    details: dict[str, object] = Field(default_factory=dict)


class RequirementResultOut(ApiModel):
    text: str
    status: Literal["MET", "MISSING", "UNCERTAIN"]
    is_optional: bool
    matched_skills: list[str]
    missing_skills: list[str]
    evidence: list[str]
    note: str | None = None


class MatchExplanationOut(ApiModel):
    why_it_may_fit: list[str]
    evidenced_qualifications: list[str]
    missing_requirements: list[str]
    uncertain_requirements: list[str]
    eligibility_concerns: list[str]
    suggested_next_step: str


class HardFilterFailureOut(ApiModel):
    code: str
    message: str
    detail: str | None = None


class MatchAssessmentOut(ApiModel):
    #: ``None`` when a hard filter excluded the job, or when there was not enough
    #: information to rank it. Never a stand-in for zero.
    score: float | None
    score_kind: ScoreKind
    score_meaning: str = Field(
        default=(
            "A ranking heuristic used to order jobs. It is not a probability of an "
            "interview or an offer."
        ),
        description="Plain-language label, carried with every score.",
    )
    passed_hard_filters: bool
    hard_filter_failures: list[HardFilterFailureOut] = Field(default_factory=list)
    components: list[MatchComponentOut] = Field(default_factory=list)
    requirement_results: list[RequirementResultOut] = Field(default_factory=list)
    explanation: MatchExplanationOut | None = None
    evidence_refs: list[dict[str, object]] = Field(default_factory=list)
    assessed_weight: float = 0.0
    engine_version: str
    assessed_at: datetime


class JobFeedItemOut(ApiModel):
    """A job plus this user's stored assessment, if one exists."""

    job: JobSummaryOut
    match: MatchAssessmentOut | None = None


class JobDetailOut(ApiModel):
    job: JobSummaryOut
    description: str | None
    requirements: list[JobRequirementOut]
    responsibilities: list[str]
    match: MatchAssessmentOut | None = None
    #: The same role found on other sources. Kept, never deleted; each preserves its
    #: own canonical application URL.
    duplicates: list[JobSummaryOut] = Field(default_factory=list)


class JobListQuery(ApiRequest):
    search: Annotated[str | None, Field(max_length=200)] = None
    company: Annotated[str | None, Field(max_length=200)] = None
    location: Annotated[str | None, Field(max_length=200)] = None
    work_arrangement: WorkArrangement | None = None
    employment_type: EmploymentType | None = None
    source: Annotated[str | None, Field(max_length=40)] = None
    include_demo: bool = True
    exclude_closed: bool = False
    min_score: Annotated[float | None, Field(ge=0.0, le=1.0)] = None
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    offset: Annotated[int, Field(ge=0)] = 0


class DiscoverRequest(ApiRequest):
    """Request body for ``POST /jobs/discover``.

    ``sources`` is a list of Greenhouse board tokens to fetch (e.g. ``["gitlab"]``
    for ``https://boards.greenhouse.io/gitlab``). An empty list re-runs every
    already-configured, enabled Greenhouse source instead of doing nothing, so the
    same endpoint covers both "try this new board" and "refresh what I have."
    """

    sources: list[str] = Field(default_factory=list, max_length=20)


class DiscoverSourceResultOut(ApiModel):
    """What happened fetching one board."""

    kind: str
    source: str = Field(description="The board token or other source-specific key.")
    status: Literal["ok", "failed"]
    jobs_seen: int
    jobs_created: int
    jobs_updated: int
    #: Previously-open postings from this source that were not seen in this fetch
    #: and so are now marked closed. The rows are kept, never deleted.
    jobs_closed: int
    error: str | None = None
    duration_ms: int
    retry_count: int


class DiscoverResponseOut(ApiModel):
    """The full result of one ``POST /jobs/discover`` call.

    Runs synchronously within the request — Phase 2 has no background worker yet
    (that arrives in Phase 4), so the response only returns once every requested
    source has been attempted.
    """

    agent_run_id: uuid.UUID
    sources: list[DiscoverSourceResultOut] = Field(default_factory=list)
