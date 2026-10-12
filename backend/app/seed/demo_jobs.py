"""Demo job postings.

Invented for this repository. Chosen to cover the cases the rest of the system has to
handle correctly rather than to look impressive:

* a posting with **no date** (``posted_at=None``, precision ``UNKNOWN``) — proves the
  UI never renders a missing date as "recent";
* a **closed** posting — proves the hard filter and the preparation check;
* an **internship** and a **contract** role — proves the employment-type filter;
* a posting with **no stated requirements** — proves a component can return "not
  assessed" instead of zero;
* the **same role from two sources** — proves cross-source deduplication links rather
  than deletes;
* a posting with a **salary band** and one without;
* remote, hybrid, and onsite arrangements.

``DEMO_SOURCE_KIND`` is a distinct connector kind, so a query can separate seeded rows
from real ones without relying on the flag alone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from app.domain.normalize import (
    EmploymentType,
    NormalizedJob,
    PostedAtPrecision,
    SalaryRange,
    SourceStatus,
    WorkArrangement,
)

#: Connector kind used for every seeded row. Never produced by a real connector.
DEMO_SOURCE_KIND: Final = "demo_seed"
DEMO_SOURCE_KEY: Final = "careerpilot-demo-v1"

#: A fixed reference date, so seeded data is deterministic across runs and the tests
#: that assert on relative ages do not drift.
_REFERENCE: Final = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _days_ago(days: int) -> datetime:
    return _REFERENCE - timedelta(days=days)


def demo_jobs() -> list[NormalizedJob]:
    """Every seeded posting. Deterministic: same list on every call."""
    return [
        NormalizedJob(
            source=DEMO_SOURCE_KIND,
            source_job_id="demo-001",
            company_name="Northwind Analytics",
            title="Backend Engineer (Python)",
            location_raw="Bengaluru, India",
            application_url="https://example.invalid/northwind/careers/backend-engineer",
            description=(
                "Northwind Analytics builds data tooling for logistics operators. You "
                "would join the platform team that owns our ingestion APIs and the "
                "job scheduler behind them."
            ),
            requirements=(
                "2+ years building backend services in Python",
                "Strong SQL and experience with PostgreSQL",
                "Experience designing and consuming REST APIs",
                "Familiarity with Docker and CI/CD pipelines",
                "Exposure to Kubernetes is a nice to have",
            ),
            responsibilities=(
                "Own and extend the ingestion API surface",
                "Improve scheduler throughput and observability",
                "Review peers' changes and keep the test suite meaningful",
            ),
            work_arrangement=WorkArrangement.HYBRID,
            employment_type=EmploymentType.FULL_TIME,
            salary=SalaryRange(minimum=1_800_000, maximum=2_800_000, currency="INR", period="year"),
            posted_at=_days_ago(6),
            posted_at_precision=PostedAtPrecision.DATE_ONLY,
            source_status=SourceStatus.OPEN,
            last_observed_at=_REFERENCE,
        ),
        NormalizedJob(
            source=DEMO_SOURCE_KIND,
            source_job_id="demo-002",
            company_name="Lumen Systems",
            title="Full Stack Developer",
            location_raw="Remote - India",
            application_url="https://example.invalid/lumen/jobs/full-stack-developer",
            description=(
                "Lumen Systems is a small, fully remote team building internal tools "
                "for clinical research groups. You would work across a React frontend "
                "and a FastAPI backend."
            ),
            requirements=(
                "Experience with React and TypeScript",
                "Comfortable writing Python services, ideally FastAPI or Django",
                "Understanding of relational data modelling",
                "Able to work independently in a distributed team",
            ),
            responsibilities=(
                "Ship features end to end, from schema to interface",
                "Write and maintain tests for what you ship",
            ),
            work_arrangement=WorkArrangement.REMOTE,
            employment_type=EmploymentType.FULL_TIME,
            # No salary stated. The UI must show "not stated", not a guess.
            posted_at=_days_ago(2),
            posted_at_precision=PostedAtPrecision.EXACT,
            source_status=SourceStatus.OPEN,
            last_observed_at=_REFERENCE,
        ),
        NormalizedJob(
            source=DEMO_SOURCE_KIND,
            source_job_id="demo-003",
            company_name="Harborline Software",
            title="Software Engineering Intern - Summer 2027",
            location_raw="Hyderabad, India",
            application_url="https://example.invalid/harborline/internships/swe-2027",
            description=(
                "A twelve-week internship on Harborline's developer-platform team. "
                "Interns are paired with a mentor and own a scoped project."
            ),
            requirements=(
                "Currently pursuing a Bachelor's degree in Computer Science or a related field",
                "Solid grounding in data structures and algorithms",
                "Familiarity with at least one of Python, Java or Go",
                "Prior internship or open-source contribution is preferred",
            ),
            responsibilities=(
                "Deliver a scoped project with your mentor",
                "Present your work at the end of the internship",
            ),
            work_arrangement=WorkArrangement.ONSITE,
            employment_type=EmploymentType.INTERNSHIP,
            salary=SalaryRange(minimum=60_000, maximum=80_000, currency="INR", period="month"),
            posted_at=_days_ago(12),
            posted_at_precision=PostedAtPrecision.DATE_ONLY,
            source_status=SourceStatus.OPEN,
            last_observed_at=_REFERENCE,
        ),
        NormalizedJob(
            source=DEMO_SOURCE_KIND,
            source_job_id="demo-004",
            company_name="Veritas Grid",
            title="Senior Platform Engineer",
            location_raw="Remote - EU",
            application_url="https://example.invalid/veritasgrid/roles/senior-platform-engineer",
            description=(
                "Veritas Grid runs settlement infrastructure for energy markets. The "
                "platform team owns the Kubernetes estate and the deployment pipeline."
            ),
            requirements=(
                "6+ years in infrastructure or platform engineering",
                "Deep experience with Kubernetes in production",
                "Terraform or equivalent infrastructure as code",
                "Strong Go or Python",
                "Experience with on-call ownership of critical systems",
            ),
            responsibilities=(
                "Own the deployment pipeline end to end",
                "Lead incident reviews and drive the fixes",
            ),
            work_arrangement=WorkArrangement.REMOTE,
            employment_type=EmploymentType.FULL_TIME,
            salary=SalaryRange(minimum=85_000, maximum=115_000, currency="EUR", period="year"),
            posted_at=_days_ago(21),
            posted_at_precision=PostedAtPrecision.DATE_ONLY,
            source_status=SourceStatus.OPEN,
            last_observed_at=_REFERENCE,
        ),
        NormalizedJob(
            source=DEMO_SOURCE_KIND,
            source_job_id="demo-005",
            company_name="Pelham Retail Group",
            title="Data Analyst",
            location_raw="Pune, India",
            application_url="https://example.invalid/pelham/careers/data-analyst",
            description=(
                "Reporting into the commercial team, this role turns store and online "
                "sales data into weekly trading reports."
            ),
            # No requirements listed at all. Skill overlap must come back "not
            # assessed" rather than scoring the candidate zero for the posting's gap.
            requirements=(),
            responsibilities=(
                "Produce the weekly trading pack",
                "Answer ad-hoc commercial questions",
            ),
            work_arrangement=WorkArrangement.ONSITE,
            employment_type=EmploymentType.FULL_TIME,
            # Date genuinely unknown: this is the case that must never render as
            # "posted recently".
            posted_at=None,
            posted_at_precision=PostedAtPrecision.UNKNOWN,
            source_status=SourceStatus.UNKNOWN,
            last_observed_at=_REFERENCE,
        ),
        NormalizedJob(
            source=DEMO_SOURCE_KIND,
            source_job_id="demo-006",
            company_name="Cobalt Interactive",
            title="Frontend Engineer (Contract)",
            location_raw="Remote",
            application_url="https://example.invalid/cobalt/contract/frontend-engineer",
            description=(
                "A six-month contract to rebuild Cobalt's customer dashboard in React, "
                "with an option to extend."
            ),
            requirements=(
                "3+ years with React",
                "Strong CSS, ideally with Tailwind CSS",
                "Experience with accessible component design",
                "Available for a six-month engagement",
            ),
            responsibilities=("Rebuild the customer dashboard", "Hand over documented components"),
            work_arrangement=WorkArrangement.REMOTE,
            employment_type=EmploymentType.CONTRACT,
            salary=SalaryRange(minimum=4_500, maximum=6_000, currency="USD", period="month"),
            posted_at=_days_ago(9),
            posted_at_precision=PostedAtPrecision.DATE_ONLY,
            source_status=SourceStatus.OPEN,
            last_observed_at=_REFERENCE,
        ),
        NormalizedJob(
            source=DEMO_SOURCE_KIND,
            source_job_id="demo-007",
            company_name="Northwind Analytics",
            title="Machine Learning Engineer",
            location_raw="Bengaluru, India",
            application_url="https://example.invalid/northwind/careers/ml-engineer",
            description=(
                "This posting has been filled. It is seeded so the closed-posting path "
                "can be exercised: the hard filter excludes it, and preparing an "
                "application against it raises JOB_CLOSED."
            ),
            requirements=(
                "3+ years applying machine learning in production",
                "Strong Python, PyTorch or TensorFlow",
                "Experience with feature pipelines",
            ),
            work_arrangement=WorkArrangement.HYBRID,
            employment_type=EmploymentType.FULL_TIME,
            posted_at=_days_ago(45),
            posted_at_precision=PostedAtPrecision.DATE_ONLY,
            source_status=SourceStatus.CLOSED,
            last_observed_at=_REFERENCE,
        ),
        NormalizedJob(
            source=DEMO_SOURCE_KIND,
            source_job_id="demo-008",
            company_name="Arcwell Labs",
            title="Junior Software Engineer",
            location_raw="Chennai, India",
            application_url="https://example.invalid/arcwell/jobs/junior-software-engineer",
            description=(
                "An entry-level role on Arcwell's integrations team, suited to recent "
                "graduates with some practical project experience."
            ),
            requirements=(
                "Bachelor's degree in Computer Science, Engineering or equivalent practical experience",
                "Working knowledge of Python or JavaScript",
                "Understanding of Git and basic testing practice",
                "Any exposure to REST APIs is a plus",
            ),
            responsibilities=("Build and maintain customer integrations",),
            work_arrangement=WorkArrangement.HYBRID,
            employment_type=EmploymentType.FULL_TIME,
            salary=SalaryRange(minimum=700_000, maximum=1_000_000, currency="INR", period="year"),
            posted_at=_days_ago(4),
            posted_at_precision=PostedAtPrecision.DATE_ONLY,
            source_status=SourceStatus.OPEN,
            last_observed_at=_REFERENCE,
        ),
    ]


def duplicate_demo_jobs() -> list[NormalizedJob]:
    """The same role as ``demo-002``, as a second source would report it.

    Seeded from a different connector kind with a different ``source_job_id`` and a
    different application URL, so the deduplication path has something real to work
    on. Both rows are kept; the feed shows the canonical one and the job detail page
    lists the other as an alternative place to apply.
    """
    return [
        NormalizedJob(
            source="career_page",
            source_job_id="lumen-careers-fsd",
            company_name="Lumen Systems Ltd.",
            title="Full Stack Developer (Remote)",
            location_raw="India, Remote",
            application_url="https://example.invalid/lumen-careers/apply/full-stack",
            description=(
                "The same opening as the Lumen Systems posting, as it appears on the "
                "company's own careers page."
            ),
            requirements=(
                "Experience with React and TypeScript",
                "Comfortable writing Python services",
            ),
            work_arrangement=WorkArrangement.REMOTE,
            employment_type=EmploymentType.FULL_TIME,
            posted_at=None,
            posted_at_precision=PostedAtPrecision.UNKNOWN,
            source_status=SourceStatus.OPEN,
            last_observed_at=_REFERENCE,
        )
    ]
