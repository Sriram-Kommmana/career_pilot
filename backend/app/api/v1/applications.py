"""Application routes, including the approval and submission endpoints.

The two consequential routes are documented at length below, because their contracts
*are* the safety guarantee:

``POST /{id}/approve`` requires the client to send the ``form_state_hash`` it
displayed. If that does not match the server's current hash, the approval is refused.
This is what stops a user approving a form that changed while they were reading it.

``POST /{id}/submit`` requires ``confirm: true`` and the same hash, then re-runs the
submission gate server-side. The gate decides, not this handler and not the client.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.api.deps import AppSettings, AuditContext, CurrentUserId, DbSession
from app.core.errors import ConflictError, NotImplementedPhaseError, ValidationError
from app.domain.states import TRACKER_COLUMNS, ApplicationState
from app.models import Application
from app.schemas.application import (
    AnswerFieldRequest,
    AnswerQuestionRequest,
    ApplicationDetailOut,
    ApplicationPackageOut,
    ApplicationReviewOut,
    ApplicationSummaryOut,
    ApproveRequest,
    BlockerOut,
    CancelRequest,
    CreateApplicationRequest,
    ManualSubmissionRequest,
    OutcomeRequest,
    ReadinessChecklistItemOut,
    ResumeGuidanceItemOut,
    SubmissionDecisionOut,
    SubmitRequest,
    TrackerBoardOut,
)
from app.schemas.common import Page
from app.schemas.job import MatchAssessmentOut
from app.services import application_package as package_service
from app.services import applications as app_service

router = APIRouter(prefix="/applications", tags=["applications"])

ApplicationId = Annotated[uuid.UUID, Path(description="Application id.")]


def _with_job_summary[ModelT: ApplicationSummaryOut](
    model: type[ModelT], application: Application
) -> ModelT:
    """Build a response model from an application row.

    Every field but ``job`` is read straight off the row; ``job`` is built by
    ``_job_summary``, which flattens the connector kind and the salary. Validating the
    row directly would fail, because the ORM ``Job`` has no ``source`` attribute - that
    value lives on ``job.job_source.kind``.
    """
    from app.api.v1.jobs import _job_summary

    payload: dict[str, object] = {
        name: getattr(application, name)
        for name in model.model_fields
        if name != "job" and hasattr(application, name)
    }
    payload["job"] = _job_summary(application.job)
    return model.model_validate(payload)


def _summary(application: Application) -> ApplicationSummaryOut:
    return _with_job_summary(ApplicationSummaryOut, application)


def _detail(application: Application) -> ApplicationDetailOut:
    return _with_job_summary(ApplicationDetailOut, application)


@router.post("", response_model=ApplicationDetailOut, status_code=status.HTTP_201_CREATED)
async def create_application(
    payload: CreateApplicationRequest, session: DbSession, user_id: CurrentUserId
) -> ApplicationDetailOut:
    """Start tracking an application.

    Returns 409 if one is already in progress for this job. Duplicate prevention is a
    partial unique index, so two concurrent requests cannot both succeed.
    """
    application = await app_service.create_application(
        session, user_id=user_id, job_id=payload.job_id, resume_id=payload.resume_id
    )
    application = await app_service.get_application(
        session, user_id=user_id, application_id=application.id
    )
    return _detail(application)


@router.get("", response_model=Page[ApplicationSummaryOut])
async def list_applications(
    session: DbSession,
    user_id: CurrentUserId,
    state: Annotated[list[ApplicationState] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ApplicationSummaryOut]:
    rows, total = await app_service.list_applications(
        session, user_id=user_id, states=state, limit=limit, offset=offset
    )
    return Page[ApplicationSummaryOut](
        items=[_summary(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get("/tracker", response_model=TrackerBoardOut)
async def tracker(session: DbSession, user_id: CurrentUserId) -> TrackerBoardOut:
    """Every application grouped into the tracker's columns.

    ``column_order`` comes from the domain layer, so the board cannot drift out of
    step with the state machine.
    """
    board = await app_service.tracker_board(session, user_id=user_id)
    return TrackerBoardOut(
        columns={
            column: [_summary(item) for item in board.get(column, [])] for column in TRACKER_COLUMNS
        },
        column_order=list(TRACKER_COLUMNS),
    )


@router.get("/{application_id}", response_model=ApplicationDetailOut)
async def get_application(
    application_id: ApplicationId, session: DbSession, user_id: CurrentUserId
) -> ApplicationDetailOut:
    application = await app_service.get_application(
        session, user_id=user_id, application_id=application_id
    )
    return _detail(application)


@router.post("/{application_id}/prepare", response_model=ApplicationDetailOut)
async def prepare_application(
    application_id: ApplicationId, session: DbSession, user_id: CurrentUserId
) -> ApplicationDetailOut:
    """Draft answers from confirmed profile facts and capture a reviewable form state.

    Fields without an authoritative source — every sensitive question among them —
    come back as ``NEEDS_HUMAN``. Nothing is submitted and no browser is opened.
    """
    await app_service.prepare(session, user_id=user_id, application_id=application_id)
    application = await app_service.get_application(
        session, user_id=user_id, application_id=application_id
    )
    return _detail(application)


@router.post("/{application_id}/answer", response_model=ApplicationDetailOut)
async def answer_field(
    application_id: ApplicationId,
    payload: AnswerFieldRequest,
    session: DbSession,
    user_id: CurrentUserId,
) -> ApplicationDetailOut:
    """Answer one outstanding field yourself.

    The answer's provenance becomes ``USER_DIRECT_INPUT``. Because this changes the
    form, the form state hash is recomputed and any existing approval is revoked.
    """
    await app_service.answer_field(
        session,
        user_id=user_id,
        application_id=application_id,
        field_key=payload.field_key,
        value=payload.value,
    )
    application = await app_service.get_application(
        session, user_id=user_id, application_id=application_id
    )
    return _detail(application)


@router.post("/{application_id}/prepare-package", response_model=ApplicationPackageOut)
async def prepare_package(
    application_id: ApplicationId,
    session: DbSession,
    user_id: CurrentUserId,
    settings: AppSettings,
) -> ApplicationPackageOut:
    """Generate (or refresh) a job-specific application package.

    Produces a fit summary (the existing match assessment — still a ranking
    heuristic, never a probability), resume-emphasis guidance, a cover-letter
    draft, and draft answers to two fixed application questions — every one of
    them grounded only in confirmed profile/resume evidence, never invented.
    Drafts are clearly marked as such (``GENERATED_UNVERIFIED``); regenerating
    never overwrites anything the user has already confirmed.
    """
    result = await package_service.prepare_package(
        session, user_id=user_id, application_id=application_id, settings=settings
    )
    # Re-fetched rather than reusing result.application directly: get_application's
    # eager-loads (notably `.job`, lazy="selectin") are what every other route in
    # this module relies on too — reusing the service's own object risks reading an
    # expired relationship outside the query that loaded it.
    application = await app_service.get_application(
        session, user_id=user_id, application_id=application_id
    )
    return ApplicationPackageOut(
        fit_summary=MatchAssessmentOut.model_validate(result.fit_summary),
        resume_guidance=[
            ResumeGuidanceItemOut(
                kind=item.kind,
                id=item.id,
                title=item.title,
                organisation=item.organisation,
                matched_skills=list(item.matched_skills),
                highlights=list(item.highlights),
                reason=item.reason,
                evidence_ref={
                    "kind": item.evidence_ref.kind,
                    "id": str(item.evidence_ref.id) if item.evidence_ref.id else None,
                    "locator": item.evidence_ref.locator,
                },
            )
            for item in result.resume_guidance
        ],
        readiness_checklist=[
            ReadinessChecklistItemOut(
                category=item.category, label=item.label, status=item.status, detail=item.detail
            )
            for item in result.readiness_checklist
        ],
        application=_detail(application),
    )


@router.post("/{application_id}/answers", response_model=ApplicationDetailOut)
async def answer_question(
    application_id: ApplicationId,
    payload: AnswerQuestionRequest,
    session: DbSession,
    user_id: CurrentUserId,
) -> ApplicationDetailOut:
    """Answer (or edit) one drafted application question yourself.

    The answer's provenance becomes ``USER_DIRECT_INPUT``. Because this changes
    the form, the form state hash is recomputed and any existing approval is
    revoked — the same rule ``/answer`` already applies to a field.
    """
    await package_service.answer_question(
        session,
        user_id=user_id,
        application_id=application_id,
        question_key=payload.question_key,
        value=payload.value,
    )
    application = await app_service.get_application(
        session, user_id=user_id, application_id=application_id
    )
    return _detail(application)


@router.post("/{application_id}/fill")
async def fill_application(
    application_id: ApplicationId, user_id: CurrentUserId
) -> dict[str, object]:
    """Open a real browser and fill the application form.

    Not implemented in Phase 1. Returning 501 is the honest answer: there is no
    browser agent yet, so nothing can be filled or verified. Phase 5 adds Playwright
    with deterministic per-ATS adapters and read-back verification.
    """
    raise NotImplementedPhaseError("Browser-based form filling", phase=5)


@router.get("/{application_id}/review", response_model=ApplicationReviewOut)
async def review_application(
    application_id: ApplicationId,
    session: DbSession,
    user_id: CurrentUserId,
    settings: AppSettings,
) -> ApplicationReviewOut:
    """Everything needed to decide whether to approve, plus the gate's current verdict.

    Safe to call at any time: it evaluates the gate without changing anything. The
    verdict shown here is advisory; the one that matters runs again inside the submit
    transaction.
    """
    application, decision = await app_service.evaluate_submission(
        session, user_id=user_id, application_id=application_id, settings=settings
    )
    documents: list[dict[str, object]] = []
    if application.resume_id is not None:
        from sqlalchemy import select

        from app.models import Resume

        resume = (
            await session.execute(select(Resume).where(Resume.id == application.resume_id))
        ).scalar_one_or_none()
        if resume is not None:
            documents.append(
                {
                    "slot": "resume",
                    "filename": resume.original_filename,
                    "content_sha256": resume.content_sha256,
                    "size_bytes": resume.size_bytes,
                }
            )

    return ApplicationReviewOut(
        application=_detail(application),
        decision=SubmissionDecisionOut(
            allowed=decision.allowed,
            blockers=[BlockerOut(**b.as_dict()) for b in decision.blockers],
        ),
        unresolved_field_labels=[
            field.label or field.field_key for field in application.fields if field.needs_human
        ],
        documents=documents,
        last_verified_at=application.verified_at,
    )


@router.post("/{application_id}/approve", response_model=ApplicationDetailOut)
async def approve_application(
    application_id: ApplicationId,
    payload: ApproveRequest,
    session: DbSession,
    user_id: CurrentUserId,
    audit: AuditContext,
) -> ApplicationDetailOut:
    """Approve the application for submission, bound to the exact form state reviewed.

    The ``form_state_hash`` in the body must equal the server's current hash. A
    mismatch means the form changed after the client rendered it, so the request is
    refused with 409 rather than recording consent to something the user did not see.
    """
    application = await app_service.get_application(
        session, user_id=user_id, application_id=application_id
    )
    if application.form_state_hash != payload.form_state_hash:
        raise ConflictError(
            "The form has changed since this page was loaded. Reload the review and "
            "check it again before approving.",
            code="APPROVAL_STALE",
            details={
                "submitted_form_state_hash": payload.form_state_hash,
                "current_form_state_hash": application.form_state_hash,
            },
        )

    await app_service.approve(
        session,
        user_id=user_id,
        application_id=application_id,
        note=payload.note,
        request_context=audit,
    )
    application = await app_service.get_application(
        session, user_id=user_id, application_id=application_id
    )
    return _detail(application)


@router.post("/{application_id}/submit", response_model=ApplicationDetailOut)
async def submit_application(
    application_id: ApplicationId,
    payload: SubmitRequest,
    session: DbSession,
    user_id: CurrentUserId,
    settings: AppSettings,
) -> ApplicationDetailOut:
    """Submit an approved, verified application.

    Three layers must all agree:

    1. ``confirm`` must be ``true`` and the ``form_state_hash`` must match — so a bare
       or replayed POST cannot submit anything.
    2. The submission gate re-runs server-side and must return no blockers.
    3. The idempotency key is claimed in the same transaction, so a double click
       cannot become two submissions.

    A refusal returns 409 with the full blocker list in ``details.blockers``.
    """
    if not payload.confirm:
        raise ValidationError(
            "Submission requires an explicit confirmation for this application.",
            details={"field": "confirm"},
        )

    application = await app_service.get_application(
        session, user_id=user_id, application_id=application_id
    )
    if application.form_state_hash != payload.form_state_hash:
        raise ConflictError(
            "The form has changed since this page was loaded. Review and approve the "
            "current version before submitting.",
            code="APPROVAL_STALE",
            details={
                "submitted_form_state_hash": payload.form_state_hash,
                "current_form_state_hash": application.form_state_hash,
            },
        )

    await app_service.submit(
        session,
        user_id=user_id,
        application_id=application_id,
        settings=settings,
        # A request that reached this handler carries an interactive bearer token.
        human_initiated=True,
    )
    application = await app_service.get_application(
        session, user_id=user_id, application_id=application_id
    )
    return _detail(application)


@router.post("/{application_id}/record-manual-submission", response_model=ApplicationDetailOut)
async def record_manual_submission(
    application_id: ApplicationId,
    payload: ManualSubmissionRequest,
    session: DbSession,
    user_id: CurrentUserId,
) -> ApplicationDetailOut:
    """Record that you submitted this application yourself, in your own browser.

    The honest path while the browser agent is still being built: CareerPilot drafts
    the answers, you apply, and the tracker keeps the record. The stored evidence
    states that CareerPilot did not verify the submission.
    """
    if not payload.confirm:
        raise ValidationError(
            "Confirm that you submitted this application yourself.",
            details={"field": "confirm"},
        )
    await app_service.record_manual_submission(
        session, user_id=user_id, application_id=application_id, note=payload.note
    )
    application = await app_service.get_application(
        session, user_id=user_id, application_id=application_id
    )
    return _detail(application)


@router.post("/{application_id}/cancel", response_model=ApplicationDetailOut)
async def cancel_application(
    application_id: ApplicationId,
    payload: CancelRequest,
    session: DbSession,
    user_id: CurrentUserId,
) -> ApplicationDetailOut:
    await app_service.cancel(
        session, user_id=user_id, application_id=application_id, reason=payload.reason
    )
    application = await app_service.get_application(
        session, user_id=user_id, application_id=application_id
    )
    return _detail(application)


@router.patch("/{application_id}/outcome", response_model=ApplicationDetailOut)
async def set_outcome(
    application_id: ApplicationId,
    payload: OutcomeRequest,
    session: DbSession,
    user_id: CurrentUserId,
) -> ApplicationDetailOut:
    """Record an interview, offer, rejection, or closure. Always a user action."""
    await app_service.set_outcome(
        session,
        user_id=user_id,
        application_id=application_id,
        outcome=payload.outcome,
        note=payload.note,
    )
    application = await app_service.get_application(
        session, user_id=user_id, application_id=application_id
    )
    return _detail(application)
