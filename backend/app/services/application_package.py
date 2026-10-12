"""Orchestrates Phase 4B's application package over existing services.

This module adds no new persistence beyond what already exists: the cover letter
is the existing ``cover_letter`` ``ApplicationField`` (part of ``BASELINE_FIELDS``
since Phase 1), and question drafts are the existing, previously-uncalled
``ApplicationAnswer`` model. Everything written here goes through the same
never-overwrite-a-user's-own-answer rule ``app.services.applications.prepare``
already applies to every other field, and the same ``_recompute_form_state`` that
already keeps ``form_state_hash``/approval invalidation correct for every other
write to an application.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain import application_package as pkg_domain
from app.domain.approval import FieldResolution, digest_value
from app.domain.provenance import ProvenanceKind
from app.domain.skills import KNOWN_TECHNOLOGIES, canonical_skill_set, extract_skill_mentions
from app.domain.states import ApplicationState
from app.models import Application, ApplicationAnswer, JobMatchAssessment
from app.services import applications as applications_service
from app.services import matching as matching_service
from app.services.jobs import get_job
from app.services.profile import get_profile

#: States prepare_package accepts — identical to what prepare() itself accepts,
#: since generating a package never makes sense before the job is even selected
#: and never makes sense once the review/submission flow has moved past READY.
_PREPARABLE_STATES = (
    ApplicationState.SELECTED,
    ApplicationState.READY,
    ApplicationState.WAITING_FOR_USER_INPUT,
    ApplicationState.FAILED,
)


@dataclass(frozen=True, slots=True)
class PackageResult:
    application: Application
    fit_summary: JobMatchAssessment
    resume_guidance: tuple[pkg_domain.ResumeGuidanceItem, ...] = field(default_factory=tuple)
    readiness_checklist: tuple[pkg_domain.ReadinessItem, ...] = field(default_factory=tuple)


def _confirmed_items(profile: object) -> tuple[pkg_domain.ConfirmedExperienceItem, ...]:
    """Canonicalises ``skills_used`` the same way ``JobRequirement.extracted_skills``
    already is, so a project's "Python" and a requirement's "python" compare equal —
    the same normalisation every other overlap check in this codebase relies on."""
    items: list[pkg_domain.ConfirmedExperienceItem] = []
    for project in profile.projects:  # type: ignore[attr-defined]
        items.append(
            pkg_domain.ConfirmedExperienceItem(
                kind="project",
                id=project.id,
                title=project.name,
                organisation=None,
                skills=canonical_skill_set(project.skills_used),
                highlights=tuple(project.highlights),
            )
        )
    for experience in profile.experience_records:  # type: ignore[attr-defined]
        items.append(
            pkg_domain.ConfirmedExperienceItem(
                kind="experience",
                id=experience.id,
                title=experience.title,
                organisation=experience.company,
                skills=canonical_skill_set(experience.skills_used),
                highlights=tuple(experience.highlights),
            )
        )
    return tuple(items)


async def prepare_package(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    settings: Settings | None = None,
) -> PackageResult:
    """Generate (or refresh) the fit summary, resume guidance, cover letter draft,
    question drafts, and readiness checklist for one application.

    Never overwrites a field or answer the user has already confirmed
    (``USER_DIRECT_INPUT``) — a regeneration only fills in what is still a draft
    or still empty, exactly like ``prepare()`` already treats every baseline field.
    """
    cfg = settings or get_settings()
    application = await applications_service.get_application(
        session, user_id=user_id, application_id=application_id
    )
    if application.state not in _PREPARABLE_STATES:
        raise ConflictError(
            f"An application in {application.state.value} cannot have its package "
            "generated.",
            code="INVALID_STATE_TRANSITION",
            details={"current_state": application.state.value},
        )

    if application.state is ApplicationState.SELECTED:
        # Never prepared yet — baseline fields (including the empty cover_letter
        # field this function fills in below) do not exist until prepare() runs.
        await applications_service.prepare(
            session, user_id=user_id, application_id=application_id
        )

    profile = await get_profile(session, user_id=user_id)
    job = await get_job(session, application.job_id)

    assessment_row = await matching_service.get_stored_assessment(
        session, user_id=user_id, job_id=job.id
    )
    if assessment_row is None:
        assessment_row, _ = await matching_service.analyze_and_store(
            session, user_id=user_id, job=job, settings=cfg
        )

    # JobRequirement.extracted_skills only reliably holds something when a
    # requirement line is itself a bare, comma-separated skill list — for the
    # realistic sentence form ("Experience with Python"), it canonicalises the
    # whole sentence as one token and matches nothing. extract_skill_mentions is
    # what the real scoring engine (app.domain.matching) trusts for this exact
    # reason — scanning the full text against the known vocabulary — so this
    # reuses that, not the stored column.
    job_requirement_skills: frozenset[str] = frozenset().union(
        *(
            extract_skill_mentions(requirement.text, vocabulary=KNOWN_TECHNOLOGIES)
            for requirement in job.requirements
        )
    )
    candidate_items = _confirmed_items(profile)
    resume_guidance = pkg_domain.build_resume_guidance(
        candidate_items=candidate_items, job_requirement_skills=job_requirement_skills
    )
    best_match = resume_guidance[0] if resume_guidance else None

    cover_letter_field = next(
        (f for f in application.fields if f.field_key == "cover_letter"), None
    )
    cover_letter_status: pkg_domain.ReadinessStatus = "missing"
    if cover_letter_field is not None and cover_letter_field.value_provenance is not (
        ProvenanceKind.USER_DIRECT_INPUT
    ):
        draft = pkg_domain.build_cover_letter_draft(
            full_name=profile.full_name,
            job_title=job.title,
            company_name=job.company.name if job.company else "this company",
            best_match=best_match,
        )
        cover_letter_field.value_masked = draft.text
        cover_letter_field.value_sha256 = digest_value(draft.text)
        cover_letter_field.value_provenance = ProvenanceKind.GENERATED_UNVERIFIED
        cover_letter_field.action_result = "generated_draft"
        # A field's resolution tracks "is there a value", independent of
        # provenance tracking "is it confirmed" — a placeholder (no real
        # evidence) is NEEDS_HUMAN like any other unanswered optional field
        # would become if required; a real generated draft is RESOLVED, same
        # as prepare()'s own resolved = value is not None and value.strip().
        cover_letter_field.resolution = (
            FieldResolution.NEEDS_HUMAN if draft.needs_human else FieldResolution.RESOLVED
        )
        cover_letter_field.needs_human = draft.needs_human
        cover_letter_status = "missing" if draft.needs_human else "needs_attention"
    elif cover_letter_field is not None:
        cover_letter_status = "ready"

    question_statuses: list[tuple[str, pkg_domain.ReadinessStatus]] = []
    existing_answers = {a.question_key: a for a in application.answers}
    for key, question_text in pkg_domain.QUESTION_TEXTS.items():
        existing = existing_answers.get(key)
        if existing is not None and existing.provenance is ProvenanceKind.USER_DIRECT_INPUT:
            question_statuses.append((key, "ready"))
            continue

        draft = pkg_domain.build_question_draft(
            key,
            job_title=job.title,
            company_name=job.company.name if job.company else "this company",
            best_match=best_match,
        )
        if existing is None:
            existing = ApplicationAnswer(
                application_id=application.id, question_key=key, question_text=question_text
            )
            session.add(existing)
        existing.question_text = question_text
        existing.answer_text = draft.text
        existing.provenance = ProvenanceKind.GENERATED_UNVERIFIED
        existing.is_generated = True
        existing.needs_human = draft.needs_human
        existing.evidence_refs = [
            {"kind": ref.kind, "id": str(ref.id) if ref.id else None, "locator": ref.locator}
            for ref in draft.evidence_refs
        ]
        question_statuses.append((key, "missing" if draft.needs_human else "needs_attention"))

    await session.flush()
    await session.refresh(application, ["fields", "answers"])
    await applications_service._recompute_form_state(
        session, application=application, user_id=user_id
    )

    outstanding_field_labels = tuple(
        f.label or f.field_key
        for f in application.fields
        if f.needs_human and f.field_key != "cover_letter"
    )
    requirement_results = assessment_row.requirement_results
    missing_requirement_texts = tuple(
        str(r["text"]) for r in requirement_results if r.get("status") == "MISSING"
    )
    uncertain_requirement_texts = tuple(
        str(r["text"]) for r in requirement_results if r.get("status") == "UNCERTAIN"
    )
    readiness_checklist = pkg_domain.build_readiness_checklist(
        outstanding_field_labels=outstanding_field_labels,
        missing_requirement_texts=missing_requirement_texts,
        uncertain_requirement_texts=uncertain_requirement_texts,
        cover_letter_status=cover_letter_status,
        question_statuses=tuple(question_statuses),
    )

    return PackageResult(
        application=application,
        fit_summary=assessment_row,
        resume_guidance=resume_guidance,
        readiness_checklist=readiness_checklist,
    )


async def answer_question(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    question_key: str,
    value: str,
) -> Application:
    """Record the user's own answer to a drafted application question.

    Mirrors ``applications.answer_field`` exactly, for ``ApplicationAnswer``
    instead of ``ApplicationField``: the result is ``USER_DIRECT_INPUT`` —
    authoritative, because the user wrote or approved it for this application —
    and the form state hash is recomputed, invalidating any stale approval.
    """
    application = await applications_service.get_application(
        session, user_id=user_id, application_id=application_id
    )
    answer = next((a for a in application.answers if a.question_key == question_key), None)
    if answer is None:
        raise NotFoundError(f"No question {question_key!r} on this application.")

    cleaned = value.strip()
    if not cleaned:
        raise ValidationError("An answer cannot be empty.", details={"question_key": question_key})

    answer.answer_text = cleaned
    answer.provenance = ProvenanceKind.USER_DIRECT_INPUT
    answer.is_generated = False
    answer.needs_human = False
    answer.user_edited_at = utcnow()

    await session.flush()
    await session.refresh(application, ["answers"])
    await applications_service._recompute_form_state(
        session, application=application, user_id=user_id
    )
    return application
