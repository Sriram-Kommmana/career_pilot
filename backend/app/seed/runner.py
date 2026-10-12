"""Loading the demo data.

Idempotent: every posting is upserted on ``(source, source_job_id)``, so running the
seeder twice updates rather than duplicates. ``--reset`` removes the seeded rows
first, and only ever touches rows whose job source is the demo connector — it cannot
delete real data.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import hash_password
from app.db.session import session_scope
from app.domain.matching import EducationLevel
from app.domain.normalize import EmploymentType
from app.domain.skills import canonical_skill
from app.models import (
    EducationRecord,
    ExperienceRecord,
    Job,
    JobSource,
    Profile,
    Project,
    Skill,
    User,
)
from app.seed.demo_jobs import DEMO_SOURCE_KEY, DEMO_SOURCE_KIND, demo_jobs, duplicate_demo_jobs
from app.services.jobs import upsert_job
from app.services.profile import refresh_skill_evidence

logger = get_logger(__name__)

#: The demo account. A development convenience, clearly labelled, and refused in
#: production by :func:`seed_database`.
# example.com is reserved for documentation by RFC 2606 and is not routable. The
# `.local` and `.invalid` TLDs are special-use and are rejected by email-validator,
# so they cannot be used for an address the API has to accept.
DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "careerpilot-demo-2026"  # noqa: S105 - documented local demo credential


async def seed_database(*, reset: bool = False, demo_user: bool = True) -> list[str]:
    """Load the demo data. Returns lines describing what happened."""
    from app.core.config import get_settings

    settings = get_settings()
    if settings.env == "production":
        raise RuntimeError(
            "Refusing to seed demo data in production. Demo jobs are not live "
            "postings and must never appear in a real deployment."
        )

    summary: list[str] = []

    async with session_scope() as session:
        if reset:
            removed = await _remove_demo_rows(session)
            summary.append(f"Removed {removed} previously seeded job row(s).")

        created = 0
        updated = 0
        for normalized in (*demo_jobs(), *duplicate_demo_jobs()):
            key = (
                DEMO_SOURCE_KEY
                if normalized.source == DEMO_SOURCE_KIND
                else f"demo-{normalized.source}"
            )
            _, was_created = await upsert_job(
                session,
                normalized=normalized,
                source_external_key=key,
                is_demo_data=True,
            )
            created += int(was_created)
            updated += int(not was_created)

        summary.append(f"Seeded {created} new and updated {updated} demo job(s).")
        summary.append("All seeded jobs are flagged is_demo_data=true and are NOT live postings.")

        if demo_user:
            user, user_created = await _ensure_demo_user(session)
            summary.append(
                f"Demo account {'created' if user_created else 'already present'}: "
                f"{DEMO_EMAIL} / {DEMO_PASSWORD}"
            )
            summary.append(f"Demo user id: {user.id}")

    return summary


async def _remove_demo_rows(session: AsyncSession) -> int:
    """Delete seeded jobs only.

    Scoped to job sources whose kind is the demo connector *and* rows flagged
    ``is_demo_data``, so there is no path by which this removes real postings.
    """
    source_ids = (
        (
            await session.execute(
                select(JobSource.id).where(JobSource.kind.in_([DEMO_SOURCE_KIND, "career_page"]))
            )
        )
        .scalars()
        .all()
    )
    if not source_ids:
        return 0
    # A demo job someone has already applied to is kept: applications.job_id is
    # ON DELETE RESTRICT, and silently destroying an application's subject would be
    # worse than leaving one stale seeded row behind.
    from app.models import Application

    applied_to = select(Application.job_id).distinct().scalar_subquery()
    result = await session.execute(
        delete(Job)
        .where(
            Job.job_source_id.in_(source_ids),
            Job.is_demo_data.is_(True),
            Job.id.notin_(applied_to),
        )
        .returning(Job.id)
    )
    removed = len(result.scalars().all())
    await session.flush()
    return removed


async def _ensure_demo_user(session: AsyncSession) -> tuple[User, bool]:
    """Create the demo account and a populated profile, or return the existing one.

    The profile is filled in deliberately so the match engine has something to work
    with: skills that overlap some postings and not others, projects and experience
    that evidence *some* of those skills, and sensitive answers left blank so the
    ``NEEDS_HUMAN`` path is visible on the review screen from the first run.
    """
    existing = (
        await session.execute(select(User).where(User.email == DEMO_EMAIL))
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    user = User(email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD), is_active=True)
    profile = Profile(
        full_name="Demo Candidate",
        headline="Backend-leaning full stack developer",
        phone="+91 98765 43210",
        location="Bengaluru, India",
        github_url="https://github.com/example-demo",
        linkedin_url="https://www.linkedin.com/in/example-demo",
        highest_education=EducationLevel.BACHELORS,
        graduation_year=2025,
        target_roles=["Backend Engineer", "Full Stack Developer", "Software Engineer"],
        preferred_locations=["Bengaluru", "Hyderabad", "Remote"],
        open_to_remote=True,
        employment_preferences=[EmploymentType.FULL_TIME.value, EmploymentType.INTERNSHIP.value],
        excluded_companies=[],
        excluded_roles=["Sales", "Business Development"],
        excluded_locations=[],
        minimum_salary=1_200_000,
        salary_currency="INR",
        # Left empty on purpose. These answers are never inferred, so the review screen
        # shows them as NEEDS_HUMAN until the user supplies them.
        work_authorisation={},
        notify_score_threshold=0.6,
    )
    user.profile = profile
    session.add(user)
    await session.flush()

    for name, category in [
        ("Python", "language"),
        ("JavaScript", "language"),
        ("TypeScript", "language"),
        ("React", "frontend"),
        ("FastAPI", "backend"),
        ("PostgreSQL", "database"),
        ("Docker", "infrastructure"),
        ("Git", "tooling"),
        ("REST API", "backend"),
        ("Data structures and algorithms", "fundamentals"),
        # Claimed but not evidenced by any project or role below, so the evidence
        # component has something to distinguish.
        ("Kubernetes", "infrastructure"),
        ("Tailwind CSS", "frontend"),
    ]:
        session.add(
            Skill(
                profile_id=profile.id,
                name=name,
                canonical_name=canonical_skill(name),
                category=category,
            )
        )

    session.add(
        EducationRecord(
            profile_id=profile.id,
            institution="Example Institute of Technology",
            degree="B.Tech",
            field_of_study="Computer Science",
            level=EducationLevel.BACHELORS,
            start_date=date(2021, 8, 1),
            end_date=date(2025, 5, 31),
            grade="8.4 CGPA",
        )
    )

    session.add(
        Project(
            profile_id=profile.id,
            name="Jobboard Aggregator",
            description=(
                "A service that pulls public job feeds, normalises them into one "
                "schema, and exposes a filtered search API."
            ),
            role="Sole author",
            repository_url="https://github.com/example-demo/jobboard-aggregator",
            skills_used=["Python", "FastAPI", "PostgreSQL", "Docker", "REST API"],
            highlights=[
                "Normalises four feed formats into a single schema",
                "Deduplicates postings across sources",
            ],
            start_date=date(2024, 6, 1),
            end_date=date(2024, 11, 30),
        )
    )
    session.add(
        Project(
            profile_id=profile.id,
            name="Study Planner",
            description="A React application for planning revision schedules.",
            role="Sole author",
            skills_used=["React", "TypeScript", "JavaScript"],
            highlights=["Offline-first with local persistence"],
            start_date=date(2023, 9, 1),
            end_date=date(2024, 1, 31),
        )
    )

    session.add(
        ExperienceRecord(
            profile_id=profile.id,
            company="Example Software Services",
            title="Software Engineering Intern",
            location="Bengaluru, India",
            employment_type=EmploymentType.INTERNSHIP,
            description="Worked on internal reporting APIs.",
            skills_used=["Python", "PostgreSQL", "Git"],
            highlights=["Added pagination and filtering to three reporting endpoints"],
            start_date=date(2024, 1, 15),
            end_date=date(2024, 7, 15),
        )
    )

    await session.flush()
    await session.refresh(profile, ["skills", "projects", "experience_records"])
    await refresh_skill_evidence(session, profile=profile)

    logger.info("demo_user_created", user_id=str(user.id))
    return user, True
