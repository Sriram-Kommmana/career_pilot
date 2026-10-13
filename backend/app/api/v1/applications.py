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
from app.browser import policy as browser_policy
from app.browser.session import manager as browser_session_manager
from app.core.errors import BrowserSessionNotFoundError, ConflictError, ValidationError
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
from app.schemas.browser import (
    BrowserSessionOut,
    ExtractedFieldOut,
    FieldFillOutcomeOut,
    FillRequest,
    FillValidationReportOut,
    InspectionResultOut,
    ProposedMappingOut,
)
from app.schemas.common import MessageResponse, Page
from app.schemas.job import MatchAssessmentOut
from app.services import application_package as package_service
from app.services import applications as app_service
from app.services import browser_fill as browser_fill_service

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


@router.post("/{application_id}/browser-session", response_model=BrowserSessionOut)
async def create_browser_session(
    application_id: ApplicationId, session: DbSession, user_id: CurrentUserId, settings: AppSettings
) -> BrowserSessionOut:
    """Open an isolated browser session for this application and navigate to it.

    An explicit action, never implicit: nothing opens a browser merely by
    loading a page. Refuses with 409 if a session for this application is
    already open — two sessions must never be able to mutate the same
    application's fields at once.
    """
    browser_session = await browser_fill_service.start_session(
        session, user_id=user_id, application_id=application_id, settings=settings
    )
    decision = browser_policy.classify_origin(browser_session.page.url)  # type: ignore[attr-defined]
    return BrowserSessionOut(
        session_id=browser_session.session_id, origin=decision.origin or "unknown"
    )


async def _resolve_browser_session_id(user_id: uuid.UUID, application_id: uuid.UUID) -> str:
    existing = await browser_session_manager.find_for_application(
        user_id=user_id, application_id=application_id
    )
    if existing is None:
        raise BrowserSessionNotFoundError(
            "No browser session is open for this application. Start one first."
        )
    return existing.session_id


@router.post("/{application_id}/browser-session/inspect", response_model=InspectionResultOut)
async def inspect_browser_session(
    application_id: ApplicationId, session: DbSession, user_id: CurrentUserId, settings: AppSettings
) -> InspectionResultOut:
    """Inspect (or re-inspect) the live form and propose a field mapping.

    Every value proposed here is grounded only in already-confirmed
    ``ApplicationField``/``ApplicationAnswer`` rows — nothing is invented, and
    a sensitive or legal question is never given a value, no matter what the
    page asks.
    """
    session_id = await _resolve_browser_session_id(user_id, application_id)
    outcome = await browser_fill_service.inspect(
        session,
        user_id=user_id,
        application_id=application_id,
        session_id=session_id,
        settings=settings,
    )
    return InspectionResultOut(
        session_id=outcome.session_id,
        origin=outcome.origin,
        supported=outcome.supported,
        reason=outcome.reason,
        fields=[
            ExtractedFieldOut(
                field_id=f.field_id,
                label=f.label,
                field_kind=f.field_kind,
                required=f.required,
                unrecognised=f.unrecognised,
            )
            for f in outcome.extracted_fields
        ],
        mappings=[
            ProposedMappingOut(
                field_id=m.field_id,
                label=m.label,
                field_kind=m.field_kind,
                required=m.required,
                proposed_value=m.proposed_value,
                source_kind=m.source_kind,
                source_id=m.source_id,
                source_provenance=m.source_provenance,
                confidence=m.confidence,
                rule_id=m.rule_id,
                needs_review=m.needs_review,
                supported_for_fill=m.supported_for_fill,
                sensitive_category=m.sensitive_category,
                reason=m.reason,
            )
            for m in outcome.proposed_mappings
        ],
        mapping_token=outcome.mapping_token,
    )


@router.post("/{application_id}/browser-session/fill", response_model=FillValidationReportOut)
async def fill_browser_session(
    application_id: ApplicationId,
    payload: FillRequest,
    session: DbSession,
    user_id: CurrentUserId,
    settings: AppSettings,
) -> FillValidationReportOut:
    """Fill only the approved fields, verify each by reading the page back,
    and never submit. This is where Approval A (the mapping token) is checked.

    Approval B (submission) is untouched by this endpoint — it still requires
    the existing ``/review`` -> ``/approve`` -> ``/submit`` path, now fed the
    real verification this endpoint produces.
    """
    session_id = await _resolve_browser_session_id(user_id, application_id)
    report = await browser_fill_service.fill(
        session,
        user_id=user_id,
        application_id=application_id,
        session_id=session_id,
        mapping_token=payload.mapping_token,
        excluded_field_ids=frozenset(payload.excluded_field_ids),
        settings=settings,
    )
    return FillValidationReportOut(
        session_id=report.session_id,
        outcomes=[
            FieldFillOutcomeOut(
                field_id=o.field_id,
                action_result=o.action_result,
                verification=o.verification,
                verification_note=o.verification_note,
            )
            for o in report.outcomes
        ],
        filled_count=report.filled_count,
        failed_count=report.failed_count,
        still_needs_human=list(report.still_needs_human),
        application_state=report.application_state,
        verification_status=report.verification_status,
    )


@router.get("/{application_id}/browser-session", response_model=BrowserSessionOut)
async def get_browser_session(
    application_id: ApplicationId, user_id: CurrentUserId
) -> BrowserSessionOut:
    """Current session status, for reload/resume. 404s if none is open —
    the same contract every other owner-scoped resource in this API uses."""
    existing = await browser_session_manager.find_for_application(
        user_id=user_id, application_id=application_id
    )
    if existing is None:
        raise BrowserSessionNotFoundError("No browser session is open for this application.")
    decision = browser_policy.classify_origin(existing.page.url)  # type: ignore[attr-defined]
    return BrowserSessionOut(session_id=existing.session_id, origin=decision.origin or "unknown")


@router.delete("/{application_id}/browser-session", response_model=MessageResponse)
async def close_browser_session(
    application_id: ApplicationId, user_id: CurrentUserId
) -> MessageResponse:
    """Explicitly close the session. Always safe to call, even if none is open."""
    existing = await browser_session_manager.find_for_application(
        user_id=user_id, application_id=application_id
    )
    if existing is not None:
        await browser_session_manager.close(existing.session_id)
    return MessageResponse(message="Browser session closed.")


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
