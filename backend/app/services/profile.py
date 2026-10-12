"""Profile reads, writes, and the bridge into the matching domain.

The important function here is :func:`build_candidate_profile`, which converts stored
rows into the pure :class:`~app.domain.matching.CandidateProfile` the match engine
consumes. Two rules are applied in that conversion and nowhere else:

* ``has_evidence`` on a skill is **derived**, never stored as user input. A skill is
  evidenced when a project or an experience record cites it. Letting a user tick
  "evidenced" themselves would make the evidence component meaningless.
* Total experience is computed from the experience records, and is ``None`` when there
  are none. It is never defaulted to zero, because "we don't know" and "no experience"
  lead to different recommendations.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.clock import utcnow
from app.core.errors import ConflictError, NotFoundError
from app.domain.matching import (
    CandidateProfile,
    CandidateSkill,
    EducationLevel,
    ProfileEvidenceItem,
)
from app.domain.normalize import EmploymentType
from app.domain.provenance import EvidenceRef
from app.domain.skills import (
    KNOWN_TECHNOLOGIES,
    canonical_skill,
    canonical_skill_set,
    extract_skill_mentions,
)
from app.models import (
    Certification,
    EducationRecord,
    ExperienceRecord,
    Profile,
    ProfileEvidence,
    Project,
    Skill,
)

#: Child collections of a profile, keyed by the API path segment that manages them.
#:
#: Typed as ``type[Any]`` because the five models share no common base beyond the
#: declarative one, and the generic CRUD helpers below only ever touch ``id`` and
#: ``profile_id``, which all five have.
CHILD_MODELS: dict[str, type[Any]] = {
    "education": EducationRecord,
    "skills": Skill,
    "projects": Project,
    "experience": ExperienceRecord,
    "certifications": Certification,
}


async def get_profile(session: AsyncSession, *, user_id: uuid.UUID) -> Profile:
    """Load a user's profile with every child collection.

    Raises :class:`NotFoundError` if it is missing, which should not happen:
    registration creates it. Treating it as an error rather than creating one here
    keeps a silent data problem from looking like normal operation.
    """
    result = await session.execute(
        select(Profile)
        .where(Profile.user_id == user_id)
        .options(
            selectinload(Profile.education_records),
            selectinload(Profile.skills),
            selectinload(Profile.projects),
            selectinload(Profile.experience_records),
            selectinload(Profile.certifications),
        )
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise NotFoundError("Profile not found for this account.")
    return profile


async def update_profile(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    changes: dict[str, object],
) -> Profile:
    """Apply a partial update.

    Only keys present in ``changes`` are written, so a PATCH that omits a field leaves
    it alone rather than clearing it.
    """
    profile = await get_profile(session, user_id=user_id)
    for key, value in changes.items():
        if not hasattr(profile, key):
            continue
        setattr(profile, key, value)
    await session.flush()
    await refresh_skill_evidence(session, profile=profile)
    return profile


async def refresh_skill_evidence(session: AsyncSession, *, profile: Profile) -> None:
    """Recompute ``has_evidence`` on every skill from projects and experience.

    Called after any change to skills, projects, or experience. Derived state is
    recomputed rather than incrementally patched, because an incremental update that
    misses a case leaves a skill permanently mislabelled as evidenced.
    """
    await session.refresh(profile, ["skills", "projects", "experience_records"])

    evidenced: set[str] = set()
    for project in profile.projects:
        evidenced.update(canonical_skill(name) for name in project.skills_used)
    for experience in profile.experience_records:
        evidenced.update(canonical_skill(name) for name in experience.skills_used)
    evidenced.discard("")

    for skill in profile.skills:
        skill.has_evidence = skill.canonical_name in evidenced
    await session.flush()


def _months_between(start: date, end: date) -> int:
    """Whole months between two dates, floored at zero."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


def compute_total_experience_months(
    records: list[ExperienceRecord], *, today: date | None = None
) -> int | None:
    """Total months of work experience, or ``None`` when it cannot be computed.

    Overlapping roles are merged rather than summed: holding two jobs at once is still
    one span of calendar time, and summing them would overstate experience.

    A record with no start date contributes nothing and is not guessed at. If no
    record has a usable span, the result is ``None`` — not zero.
    """
    if not records:
        return None
    reference = today or utcnow().date()

    spans: list[tuple[date, date]] = []
    for record in records:
        if record.start_date is None:
            continue
        end = record.end_date or (reference if record.is_current else None)
        if end is None:
            # Not current and no end date: the span is unknown, so it is skipped
            # rather than assumed to run to today.
            continue
        if end < record.start_date:
            continue
        spans.append((record.start_date, end))

    if not spans:
        return None

    spans.sort()
    merged: list[tuple[date, date]] = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    return sum(_months_between(start, end) for start, end in merged)


async def get_confirmed_profile_evidence(
    session: AsyncSession, *, user_id: uuid.UUID
) -> list[ProfileEvidence]:
    """Confirmed free-text evidence (e.g. resume excerpts) for the match engine.

    ``approved_at`` is the gate, same principle as ``Resume.extraction_confirmed_at``:
    a row that exists but has not been explicitly approved is not used for matching.
    """
    result = await session.execute(
        select(ProfileEvidence).where(
            ProfileEvidence.user_id == user_id, ProfileEvidence.approved_at.is_not(None)
        )
    )
    return list(result.scalars().all())


def build_candidate_profile(
    profile: Profile,
    *,
    today: date | None = None,
    profile_evidence: Sequence[ProfileEvidence] = (),
) -> CandidateProfile:
    """Convert stored rows into the pure input the match engine takes.

    This is the only place the ORM and the matching domain meet, which is what lets
    ``app.domain.matching`` stay free of SQLAlchemy and be unit-tested on plain
    dataclasses.
    """
    evidence_by_skill: dict[str, list[EvidenceRef]] = {}

    for project in profile.projects:
        # EvidenceRef.describe() already prefixes the kind, so the locator carries
        # only the human-checkable part.
        ref = EvidenceRef(kind="project", id=project.id, locator=project.name)
        for raw in project.skills_used:
            key = canonical_skill(raw)
            if key:
                evidence_by_skill.setdefault(key, []).append(ref)

    for experience in profile.experience_records:
        ref = EvidenceRef(
            kind="experience",
            id=experience.id,
            locator=f"{experience.title} at {experience.company}",
        )
        for raw in experience.skills_used:
            key = canonical_skill(raw)
            if key:
                evidence_by_skill.setdefault(key, []).append(ref)

    skills = tuple(
        CandidateSkill(
            name=skill.name,
            has_evidence=skill.canonical_name in evidence_by_skill,
            evidence=tuple(evidence_by_skill.get(skill.canonical_name, ())),
            years=skill.years,
        )
        for skill in profile.skills
    )

    evidence_items = tuple(
        ProfileEvidenceItem(
            content=row.content,
            skills=canonical_skill_set(row.skills)
            | extract_skill_mentions(row.content, vocabulary=KNOWN_TECHNOLOGIES),
            locator=row.locator,
            evidence_ref=EvidenceRef(
                kind=row.source_kind, id=row.id, locator=row.locator, excerpt=row.content
            ),
        )
        for row in profile_evidence
    )

    employment_preferences: list[EmploymentType] = []
    for raw in profile.employment_preferences:
        try:
            employment_preferences.append(EmploymentType(raw))
        except ValueError:
            # A stale or hand-edited value is ignored rather than crashing the feed.
            continue

    return CandidateProfile(
        skills=skills,
        highest_education=profile.highest_education or EducationLevel.UNKNOWN,
        graduation_year=profile.graduation_year,
        total_experience_months=compute_total_experience_months(
            list(profile.experience_records), today=today
        ),
        target_roles=tuple(profile.target_roles),
        preferred_locations=tuple(profile.preferred_locations),
        open_to_remote=profile.open_to_remote,
        employment_preferences=tuple(employment_preferences),
        excluded_companies=tuple(profile.excluded_companies),
        excluded_roles=tuple(profile.excluded_roles),
        excluded_locations=tuple(profile.excluded_locations),
        minimum_salary=float(profile.minimum_salary) if profile.minimum_salary else None,
        salary_currency=profile.salary_currency,
        profile_evidence=evidence_items,
    )


# ---------------------------------------------------------------------------
# Child records
# ---------------------------------------------------------------------------


async def add_child(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    collection: str,
    payload: dict[str, object],
) -> object:
    """Add one record to a profile child collection."""
    profile = await get_profile(session, user_id=user_id)
    model: Any = CHILD_MODELS[collection]

    if model is Skill:
        name = str(payload.get("name", "")).strip()
        payload = {**payload, "canonical_name": canonical_skill(name)}

    record = model(profile_id=profile.id, **payload)
    session.add(record)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if model is Skill:
            raise ConflictError(
                "That skill is already on your profile.",
                code="DUPLICATE_SKILL",
                details={"canonical_name": payload.get("canonical_name")},
            ) from exc
        raise ConflictError(
            f"That {collection[:-1] if collection.endswith('s') else collection} "
            "record could not be added.",
            code="DUPLICATE_RECORD",
        ) from exc

    if collection in ("skills", "projects", "experience"):
        await refresh_skill_evidence(session, profile=profile)
    return record


async def update_child(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    collection: str,
    record_id: uuid.UUID,
    payload: dict[str, object],
) -> object:
    """Update one record, after confirming the caller owns it."""
    profile = await get_profile(session, user_id=user_id)
    model: Any = CHILD_MODELS[collection]

    result = await session.execute(
        select(model).where(model.id == record_id, model.profile_id == profile.id)
    )
    record: Any = result.scalar_one_or_none()
    if record is None:
        # 404 rather than 403: the API does not confirm that someone else's id exists.
        raise NotFoundError(f"No such {collection} record.")

    for key, value in payload.items():
        if hasattr(record, key):
            setattr(record, key, value)
    if model is Skill and "name" in payload:
        record.canonical_name = canonical_skill(str(payload["name"]))

    await session.flush()
    if collection in ("skills", "projects", "experience"):
        await refresh_skill_evidence(session, profile=profile)
    return record


async def delete_child(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    collection: str,
    record_id: uuid.UUID,
) -> None:
    """Delete one record, after confirming the caller owns it."""
    profile = await get_profile(session, user_id=user_id)
    model: Any = CHILD_MODELS[collection]

    result = await session.execute(
        delete(model)
        .where(model.id == record_id, model.profile_id == profile.id)
        .returning(model.id)
    )
    if result.scalar_one_or_none() is None:
        raise NotFoundError(f"No such {collection} record.")

    await session.flush()
    if collection in ("skills", "projects", "experience"):
        await refresh_skill_evidence(session, profile=profile)
