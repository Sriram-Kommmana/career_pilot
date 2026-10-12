"""Applications: creation, preparation, approval, and the enforced submission gate.

Every state change goes through :func:`transition`, which validates the move against
``app.domain.states`` and appends an event. There is no other way to write
``application.state``, so an application's history is always complete and an illegal
move raises instead of corrupting it.

The submission endpoint calls :func:`submit`, which re-evaluates
``app.domain.submission_gate`` inside the same transaction that claims the idempotency
key. The frontend's button is a convenience; this function is the boundary.

Phase 1 has no browser, so nothing reaches ``WAITING_FOR_APPROVAL`` yet and
:func:`submit` correctly refuses with ``NO_VERIFICATION``. That refusal is the
feature, and ``tests/integration/test_submission_endpoint.py`` asserts it.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.clock import utcnow
from app.core.config import Settings, get_settings
from app.core.errors import (
    ConflictError,
    DuplicateApplicationError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_correlation_id, get_logger
from app.domain.approval import (
    AnswerSnapshot,
    ApprovalRecord,
    DocumentSnapshot,
    FieldResolution,
    FieldSnapshot,
    FormSnapshot,
    VerificationStatus,
    compute_form_state_hash,
    digest_value,
)
from app.domain.provenance import ProvenanceKind, SensitiveCategory
from app.domain.redaction import Sensitivity, redact_for_storage
from app.domain.states import (
    ApplicationOutcome,
    ApplicationState,
    InterventionReason,
    assert_transition,
    tracker_column,
)
from app.domain.submission_gate import SubmissionContext, SubmissionDecision
from app.domain.submission_gate import evaluate as evaluate_gate
from app.models import (
    Application,
    ApplicationEvent,
    ApplicationField,
    ApprovalRecordRow,
    Job,
    Resume,
)
from app.services.jobs import get_job
from app.services.profile import get_profile

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# The baseline field set
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldPlan:
    """One field CareerPilot knows how to prepare.

    ``profile_attr`` names where an authoritative value comes from. ``None`` means
    there is no automatic source, so the field always becomes ``NEEDS_HUMAN`` — which
    is the correct answer for a sensitive question, not a gap to be filled in later.
    """

    key: str
    label: str
    field_type: str
    required: bool
    profile_attr: str | None = None
    sensitive_category: SensitiveCategory | None = None


#: The questions essentially every ATS application form asks. Phase 5's browser
#: adapters map a real page's fields onto these keys; until then this set is what the
#: review screen shows, with the same provenance rules applied.
BASELINE_FIELDS: Final[tuple[FieldPlan, ...]] = (
    FieldPlan("full_name", "Full name", "text", True, "full_name"),
    FieldPlan("email", "Email address", "email", True, "email"),
    FieldPlan("phone", "Phone number", "tel", False, "phone"),
    FieldPlan("location", "Current location", "text", False, "location"),
    FieldPlan("linkedin_url", "LinkedIn profile", "text", False, "linkedin_url"),
    FieldPlan("github_url", "GitHub profile", "text", False, "github_url"),
    FieldPlan("portfolio_url", "Portfolio or website", "text", False, "portfolio_url"),
    FieldPlan("resume", "Resume", "file", True, None),
    # No automatic source, by design. See docs/SAFETY.md section 4: a wrong answer to
    # any of these is a misrepresentation, not a UX inconvenience.
    FieldPlan(
        "work_authorisation",
        "Are you authorised to work in this location?",
        "radio",
        True,
        None,
        SensitiveCategory.WORK_AUTHORISATION,
    ),
    FieldPlan(
        "requires_sponsorship",
        "Will you now or in the future require visa sponsorship?",
        "radio",
        True,
        None,
        SensitiveCategory.VISA_SPONSORSHIP,
    ),
    FieldPlan(
        "salary_expectation",
        "Salary expectation",
        "text",
        False,
        None,
        SensitiveCategory.SALARY_EXPECTATION,
    ),
    FieldPlan("cover_letter", "Cover letter", "textarea", False, None),
)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def get_application(
    session: AsyncSession, *, user_id: uuid.UUID, application_id: uuid.UUID
) -> Application:
    """Load an application the caller owns.

    A row belonging to someone else raises :class:`NotFoundError`, not a 403: the API
    does not confirm that another user's application id exists.
    """
    result = await session.execute(
        select(Application)
        .where(Application.id == application_id, Application.user_id == user_id)
        .options(
            # job_source is needed for the response payload's `source` field, and a
            # lazy load would raise MissingGreenlet under the asyncio driver.
            selectinload(Application.job).selectinload(Job.company),
            selectinload(Application.job).selectinload(Job.job_source),
            selectinload(Application.fields),
            selectinload(Application.answers),
            selectinload(Application.events),
            selectinload(Application.approvals),
        )
    )
    application = result.scalar_one_or_none()
    if application is None:
        raise NotFoundError("Application not found.")
    return application


async def list_applications(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    states: list[ApplicationState] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Application], int]:
    base = select(Application).where(Application.user_id == user_id)
    if states:
        base = base.where(Application.state.in_(states))

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    rows = (
        (
            await session.execute(
                base.options(
                    selectinload(Application.job).selectinload(Job.company),
                    selectinload(Application.job).selectinload(Job.job_source),
                )
                .order_by(Application.updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def tracker_board(
    session: AsyncSession, *, user_id: uuid.UUID
) -> dict[str, list[Application]]:
    """Every application, grouped into the tracker's columns."""
    rows = (
        (
            await session.execute(
                select(Application)
                .where(Application.user_id == user_id)
                .options(
                    selectinload(Application.job).selectinload(Job.company),
                    selectinload(Application.job).selectinload(Job.job_source),
                )
                .order_by(Application.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )

    board: dict[str, list[Application]] = {}
    for application in rows:
        board.setdefault(tracker_column(application.state), []).append(application)
    return board


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


async def transition(
    session: AsyncSession,
    *,
    application: Application,
    to_state: ApplicationState,
    event_type: str = "state_changed",
    message: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    payload: dict[str, object] | None = None,
) -> Application:
    """Move an application to a new state and record the event.

    The only writer of ``application.state``. ``assert_transition`` raises
    :class:`~app.core.errors.InvalidStateTransitionError` for an illegal move, which
    the API surfaces as a 409 listing the states that *are* reachable.
    """
    from_state = application.state
    assert_transition(from_state, to_state)
    application.state = to_state

    session.add(
        ApplicationEvent(
            application_id=application.id,
            event_type=event_type,
            from_state=from_state,
            to_state=to_state,
            message=message,
            payload=payload or {},
            correlation_id=get_correlation_id(),
            actor_user_id=actor_user_id,
        )
    )
    await session.flush()
    logger.info(
        "application_state_changed",
        application_id=str(application.id),
        from_state=from_state.value,
        to_state=to_state.value,
    )
    return application


async def record_event(
    session: AsyncSession,
    *,
    application: Application,
    event_type: str,
    message: str | None = None,
    intervention_reason: InterventionReason | None = None,
    actor_user_id: uuid.UUID | None = None,
    payload: dict[str, object] | None = None,
) -> None:
    """Append an event without changing state."""
    session.add(
        ApplicationEvent(
            application_id=application.id,
            event_type=event_type,
            intervention_reason=intervention_reason,
            message=message,
            payload=payload or {},
            correlation_id=get_correlation_id(),
            actor_user_id=actor_user_id,
        )
    )
    await session.flush()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def create_application(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    resume_id: uuid.UUID | None = None,
) -> Application:
    """Start tracking an application for a job.

    Duplicate prevention is enforced by the partial unique index on
    ``(user_id, job_id)`` for non-terminal states, so two concurrent requests cannot
    both succeed. The pre-check exists only to produce a better error message.
    """
    job = await get_job(session, job_id)

    existing = (
        await session.execute(
            select(Application).where(
                Application.user_id == user_id,
                Application.job_id == job_id,
                Application.state.notin_([ApplicationState.CANCELLED, ApplicationState.FAILED]),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateApplicationError(
            "You already have an application in progress for this job.",
            details={
                "application_id": str(existing.id),
                "state": existing.state.value,
                "hint": ("Cancel the existing application first if you want to start over."),
            },
        )

    if resume_id is not None:
        await _require_resume(session, user_id=user_id, resume_id=resume_id)

    application = Application(
        user_id=user_id,
        job_id=job.id,
        resume_id=resume_id,
        state=ApplicationState.SELECTED,
        # Snapshotted now, so a later edit to the posting cannot silently redirect a
        # reviewed application to a different URL.
        application_url=job.application_url,
    )
    session.add(application)

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateApplicationError(
            "You already have an application in progress for this job."
        ) from exc

    session.add(
        ApplicationEvent(
            application_id=application.id,
            event_type="created",
            to_state=ApplicationState.SELECTED,
            message=f"Tracking an application for {job.title}.",
            correlation_id=get_correlation_id(),
            actor_user_id=user_id,
        )
    )
    await session.flush()
    return application


async def _require_resume(
    session: AsyncSession, *, user_id: uuid.UUID, resume_id: uuid.UUID
) -> Resume:
    result = await session.execute(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id)
    )
    resume = result.scalar_one_or_none()
    if resume is None:
        raise NotFoundError("Resume not found.")
    return resume


# ---------------------------------------------------------------------------
# Prepare
# ---------------------------------------------------------------------------


async def prepare(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
) -> Application:
    """Draft the application and capture a reviewable form state.

    Each baseline field is filled only from an authoritative fact. Anything without
    one — notably every sensitive question — becomes ``NEEDS_HUMAN``. The application
    therefore lands in ``READY`` only when nothing is outstanding, and in
    ``WAITING_FOR_USER_INPUT`` otherwise.

    Nothing is submitted, and no browser is opened.
    """
    application = await get_application(session, user_id=user_id, application_id=application_id)

    if application.state not in (
        ApplicationState.SELECTED,
        ApplicationState.READY,
        ApplicationState.WAITING_FOR_USER_INPUT,
        ApplicationState.FAILED,
    ):
        raise ConflictError(
            f"An application in {application.state.value} cannot be prepared.",
            code="INVALID_STATE_TRANSITION",
            details={"current_state": application.state.value},
        )

    await transition(
        session,
        application=application,
        to_state=ApplicationState.PREPARING,
        message="Drafting answers from your confirmed profile.",
        actor_user_id=user_id,
    )

    job = await get_job(session, application.job_id)

    # The posting must still be open. A closed job is a dead end, and saying so now is
    # better than discovering it at submission time.
    from app.domain.normalize import SourceStatus

    if job.source_status is SourceStatus.CLOSED:
        await record_event(
            session,
            application=application,
            event_type="intervention_raised",
            intervention_reason=InterventionReason.JOB_CLOSED,
            message="The source reports this posting as closed.",
        )
        application.unresolved_interventions = [InterventionReason.JOB_CLOSED.value]
        await transition(
            session,
            application=application,
            to_state=ApplicationState.WAITING_FOR_USER_INPUT,
            message="This posting appears to be closed. Check it before going further.",
            actor_user_id=user_id,
        )
        return application

    profile = await get_profile(session, user_id=user_id)
    resume = await _select_resume(session, application=application, user_id=user_id)

    sources: dict[str, str | None] = {
        "full_name": profile.full_name,
        "email": profile.user.email if profile.user else None,
        "phone": profile.phone,
        "location": profile.location,
        "linkedin_url": profile.linkedin_url,
        "github_url": profile.github_url,
        "portfolio_url": profile.portfolio_url,
    }

    existing_fields = {field.field_key: field for field in application.fields}
    needs_human_labels: list[str] = []

    for plan in BASELINE_FIELDS:
        value: str | None = None
        provenance = ProvenanceKind.UNKNOWN

        if plan.key == "resume":
            if resume is not None:
                value = resume.original_filename
                provenance = ProvenanceKind.USER_CONFIRMED_PROFILE
        elif plan.key == "work_authorisation":
            answer = profile.work_authorisation.get("authorised")
            if isinstance(answer, str) and answer.strip():
                value = answer.strip()
                provenance = ProvenanceKind.USER_CONFIRMED_PROFILE
        elif plan.key == "requires_sponsorship":
            answer = profile.work_authorisation.get("requires_sponsorship")
            if isinstance(answer, str) and answer.strip():
                value = answer.strip()
                provenance = ProvenanceKind.USER_CONFIRMED_PROFILE
        elif plan.profile_attr is not None:
            raw = sources.get(plan.profile_attr)
            if raw is not None and str(raw).strip():
                value = str(raw).strip()
                provenance = ProvenanceKind.USER_CONFIRMED_PROFILE

        # A field already answered by the user keeps that answer; re-preparing must
        # not discard work the user did.
        previous = existing_fields.get(plan.key)
        if (
            previous is not None
            and previous.value_provenance is ProvenanceKind.USER_DIRECT_INPUT
            and previous.value_masked
        ):
            value = previous.value_masked
            provenance = ProvenanceKind.USER_DIRECT_INPUT

        resolved = value is not None and value.strip() != ""
        stored_value, sensitivity = redact_for_storage(
            field_name=plan.key, value=value, label=plan.label
        )

        # An unresolved *optional* field is not something a human must answer before
        # going further — it is simply skipped, exactly as FieldResolution.SKIPPED
        # describes. Marking it NEEDS_HUMAN would block a required-fields-only flow on
        # an empty LinkedIn URL or cover letter, which is not what "required" means.
        if resolved:
            resolution = FieldResolution.RESOLVED
        elif plan.required:
            resolution = FieldResolution.NEEDS_HUMAN
        else:
            resolution = FieldResolution.SKIPPED
        blocks_readiness = resolution is FieldResolution.NEEDS_HUMAN

        field = previous or ApplicationField(application_id=application.id, field_key=plan.key)
        field.label = plan.label
        field.field_type = plan.field_type
        field.is_required = plan.required
        field.resolution = resolution
        field.sensitivity = sensitivity
        field.value_masked = stored_value
        field.value_sha256 = digest_value(value) if value is not None else None
        field.value_provenance = provenance
        field.sensitive_category = plan.sensitive_category
        field.needs_human = blocks_readiness
        # No browser has run, so no field has been verified against a live page.
        field.verification = VerificationStatus.NOT_ATTEMPTED
        if resolved:
            field.action_result = "prepared"
        elif blocks_readiness:
            field.action_result = "awaiting_user"
        else:
            field.action_result = "skipped_optional"
        if previous is None:
            session.add(field)

        if blocks_readiness:
            needs_human_labels.append(plan.label)

    await session.flush()
    await session.refresh(application, ["fields", "answers"])

    application.application_url = job.application_url
    application.form_state_hash = compute_form_state_hash(
        build_form_snapshot(application, resume=resume)
    )
    application.verification_status = VerificationStatus.NOT_ATTEMPTED
    application.verified_at = None
    application.resume_id = resume.id if resume is not None else application.resume_id

    if needs_human_labels:
        application.unresolved_interventions = [InterventionReason.NEEDS_HUMAN.value]
        await transition(
            session,
            application=application,
            to_state=ApplicationState.WAITING_FOR_USER_INPUT,
            message=f"{len(needs_human_labels)} question(s) need your answer.",
            actor_user_id=user_id,
            payload={"needs_human": needs_human_labels[:20]},
        )
    else:
        application.unresolved_interventions = []
        await transition(
            session,
            application=application,
            to_state=ApplicationState.READY,
            message="Draft prepared. Review it before anything is filled in.",
            actor_user_id=user_id,
        )
    return application


async def _select_resume(
    session: AsyncSession, *, application: Application, user_id: uuid.UUID
) -> Resume | None:
    """The resume to use: the one already chosen, else the user's default, else newest."""
    if application.resume_id is not None:
        return await _require_resume(session, user_id=user_id, resume_id=application.resume_id)

    result = await session.execute(
        select(Resume)
        .where(Resume.user_id == user_id)
        .order_by(Resume.is_default.desc(), Resume.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def answer_field(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    field_key: str,
    value: str,
) -> Application:
    """Record a user's own answer to an outstanding field.

    The resulting provenance is ``USER_DIRECT_INPUT`` — authoritative, because the
    user typed it for this application. Answering changes the form, so the form state
    hash is recomputed and any existing approval is invalidated.
    """
    application = await get_application(session, user_id=user_id, application_id=application_id)
    field = next((f for f in application.fields if f.field_key == field_key), None)
    if field is None:
        raise NotFoundError(f"No field {field_key!r} on this application.")

    cleaned = value.strip()
    if not cleaned:
        raise ValidationError("An answer cannot be empty.", details={"field": field_key})

    stored_value, sensitivity = redact_for_storage(
        field_name=field.field_key, value=cleaned, label=field.label
    )
    field.value_masked = stored_value
    field.sensitivity = sensitivity
    field.value_sha256 = digest_value(cleaned)
    field.value_provenance = ProvenanceKind.USER_DIRECT_INPUT
    field.resolution = FieldResolution.RESOLVED
    field.needs_human = False
    field.action_result = "user_answered"
    # Still unverified: a value in our database is not a value on the employer's page.
    field.verification = VerificationStatus.NOT_ATTEMPTED

    await session.flush()
    await session.refresh(application, ["fields"])

    await _recompute_form_state(session, application=application, user_id=user_id)

    outstanding = [f.label or f.field_key for f in application.fields if f.needs_human]
    if not outstanding and application.state is ApplicationState.WAITING_FOR_USER_INPUT:
        application.unresolved_interventions = []
        await transition(
            session,
            application=application,
            to_state=ApplicationState.READY,
            message="All outstanding questions answered.",
            actor_user_id=user_id,
        )
    return application


async def _recompute_form_state(
    session: AsyncSession, *, application: Application, user_id: uuid.UUID
) -> None:
    """Recompute the form hash and revoke any approval it invalidates.

    This is the mechanism behind safety requirement 3. Rather than remembering to
    invalidate approvals at each call site, any change that alters the form state hash
    revokes them here.
    """
    resume = (
        await _require_resume(session, user_id=user_id, resume_id=application.resume_id)
        if application.resume_id
        else None
    )
    new_hash = compute_form_state_hash(build_form_snapshot(application, resume=resume))
    if new_hash == application.form_state_hash:
        return

    previous_hash = application.form_state_hash
    application.form_state_hash = new_hash
    # The reviewed form no longer exists, so the verification against it is void too.
    application.verification_status = VerificationStatus.NOT_ATTEMPTED
    application.verified_at = None

    revoked = 0
    for approval in application.approvals:
        if approval.revoked_at is None:
            approval.revoked_at = utcnow()
            approval.revoked_reason = "The form changed after this approval was given."
            revoked += 1

    if revoked:
        await record_event(
            session,
            application=application,
            event_type="approval_invalidated",
            message=(
                "Your approval was invalidated because the application form changed. "
                "Review the current version and approve again."
            ),
            payload={
                "revoked_approvals": revoked,
                "previous_form_state_hash": previous_hash,
                "current_form_state_hash": new_hash,
            },
        )
    await session.flush()


def build_form_snapshot(application: Application, *, resume: Resume | None) -> FormSnapshot:
    """The pure snapshot of what a human would review.

    Sensitive fields contribute their digest rather than their value, so the hash
    still changes when the value does without the plaintext being needed here.

    Precondition: ``application.fields`` and ``application.answers`` must already be
    loaded. Both are read here, and a lazy load under the asyncio driver raises
    ``MissingGreenlet`` rather than quietly issuing a query. Every caller reaches this
    through :func:`get_application`, which eager-loads both.
    """
    fields = tuple(
        FieldSnapshot(
            field_id=field.field_key,
            label=field.label or field.field_key,
            resolution=field.resolution,
            required=field.is_required,
            value_for_hash=(
                field.value_sha256
                if field.sensitivity is not Sensitivity.PUBLIC
                else field.value_masked
            ),
            verification=field.verification,
            is_sensitive=field.sensitivity is not Sensitivity.PUBLIC,
        )
        for field in application.fields
    )
    documents = (
        (
            DocumentSnapshot(
                slot="resume",
                filename=resume.original_filename,
                content_sha256=resume.content_sha256,
            ),
        )
        if resume is not None
        else ()
    )
    answers = tuple(
        AnswerSnapshot(
            question_key=answer.question_key,
            value_sha256=digest_value(answer.answer_text or ""),
            provenance=answer.provenance.value,
        )
        for answer in application.answers
    )
    return FormSnapshot(
        application_url=application.application_url,
        fields=fields,
        documents=documents,
        answers=answers,
    )


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def _domain_approval(application: Application) -> ApprovalRecord | None:
    """The most recent active approval, as the gate's pure dataclass."""
    active = [a for a in application.approvals if a.revoked_at is None]
    if not active:
        return None
    latest = max(active, key=lambda a: a.approved_at)
    return ApprovalRecord(
        application_id=str(latest.application_id),
        approved_by_user_id=str(latest.approved_by_user_id),
        approved_at=latest.approved_at,
        form_state_hash=latest.form_state_hash,
        note=latest.note,
        revoked_at=latest.revoked_at,
    )


def build_submission_context(
    application: Application,
    *,
    requesting_user_id: uuid.UUID,
    resume: Resume | None = None,
    human_initiated: bool = True,
    job_closed: bool | None = None,
) -> SubmissionContext:
    """Assemble the gate's inputs from database rows.

    Nothing here is derived from model output. ``human_initiated`` is set by the API
    layer from the request itself, and is ``False`` for any non-interactive caller.
    """
    from app.domain.normalize import SourceStatus

    closed = (
        job_closed
        if job_closed is not None
        else (application.job is not None and application.job.source_status is SourceStatus.CLOSED)
    )
    return SubmissionContext(
        application_id=str(application.id),
        owner_user_id=str(application.user_id),
        requesting_user_id=str(requesting_user_id),
        state=application.state,
        current_form_state_hash=application.form_state_hash,
        approval=_domain_approval(application),
        verification_status=application.verification_status,
        verified_at=application.verified_at,
        form_snapshot=build_form_snapshot(application, resume=resume),
        unresolved_interventions=tuple(
            InterventionReason(reason) for reason in application.unresolved_interventions
        ),
        already_submitted=application.already_submitted,
        job_closed=closed,
        human_initiated=human_initiated,
    )


async def evaluate_submission(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    settings: Settings | None = None,
    human_initiated: bool = True,
) -> tuple[Application, SubmissionDecision]:
    """Run the gate without submitting. Drives the review screen."""
    cfg = settings or get_settings()
    application = await get_application(session, user_id=user_id, application_id=application_id)
    resume = (
        await _require_resume(session, user_id=user_id, resume_id=application.resume_id)
        if application.resume_id
        else None
    )
    context = build_submission_context(
        application,
        requesting_user_id=user_id,
        resume=resume,
        human_initiated=human_initiated,
    )
    decision = evaluate_gate(
        context,
        now=utcnow(),
        max_verification_age=timedelta(seconds=cfg.max_verification_age_seconds),
    )
    return application, decision


async def approve(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    note: str | None = None,
    request_context: dict[str, object] | None = None,
) -> Application:
    """Record the user's consent to submit the form exactly as it stands.

    Requires ``WAITING_FOR_APPROVAL``: an approval is consent to submit a *verified*
    form, and a form that has not been filled and read back has nothing to consent to.
    The approval is bound to the current ``form_state_hash``, so any later change
    invalidates it.
    """
    application = await get_application(session, user_id=user_id, application_id=application_id)

    if application.state is not ApplicationState.WAITING_FOR_APPROVAL:
        raise ConflictError(
            "An application can only be approved once its form has been filled and "
            f"verified. This one is in {application.state.value}.",
            code="INVALID_STATE_TRANSITION",
            details={
                "current_state": application.state.value,
                "required_state": ApplicationState.WAITING_FOR_APPROVAL.value,
            },
        )
    if application.form_state_hash is None:
        raise ConflictError("There is no captured form state to approve.", code="NO_FORM_STATE")

    # Any earlier approval is superseded, so only one is ever active.
    for existing in application.approvals:
        if existing.revoked_at is None:
            existing.revoked_at = utcnow()
            existing.revoked_reason = "Superseded by a newer approval."

    session.add(
        ApprovalRecordRow(
            application_id=application.id,
            approved_by_user_id=user_id,
            approved_at=utcnow(),
            form_state_hash=application.form_state_hash,
            note=note,
            request_context=request_context or {},
        )
    )
    await record_event(
        session,
        application=application,
        event_type="approved",
        message="You approved this application for submission.",
        actor_user_id=user_id,
        payload={"form_state_hash": application.form_state_hash},
    )
    await session.flush()
    await session.refresh(application, ["approvals"])
    return application


async def submit(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    settings: Settings | None = None,
    human_initiated: bool = True,
) -> Application:
    """Submit an application, if and only if the gate permits it.

    The gate is re-evaluated here, inside the transaction that claims the idempotency
    key, so a form that changed between the review screen and the button press is
    caught. A refusal raises :class:`~app.core.errors.SubmissionBlockedError` carrying
    every blocker.

    Phase 1 has no browser, so this always refuses with ``NO_VERIFICATION``. The
    actual submission step arrives with Phase 5 and 6; nothing here pretends to have
    sent anything.
    """
    from app.core.errors import SubmissionBlockedError

    cfg = settings or get_settings()
    application, decision = await evaluate_submission(
        session,
        user_id=user_id,
        application_id=application_id,
        settings=cfg,
        human_initiated=human_initiated,
    )

    if not decision.allowed:
        await record_event(
            session,
            application=application,
            event_type="submission_refused",
            message="Submission was refused because conditions were not met.",
            actor_user_id=user_id,
            payload={"blockers": [b.code.value for b in decision.blockers]},
        )
        raise SubmissionBlockedError(
            "This application cannot be submitted yet.",
            details={"blockers": [b.as_dict() for b in decision.blockers]},
        )

    # Claim the idempotency key before doing anything outward-facing, so a crash
    # mid-submit cannot be retried into a second real application.
    application.submission_idempotency_key = secrets.token_hex(16)
    application.submission_attempted_at = utcnow()
    await transition(
        session,
        application=application,
        to_state=ApplicationState.SUBMITTING,
        message="Submitting the approved application.",
        actor_user_id=user_id,
    )

    # The browser step belongs to Phase 5/6. Until it exists, this function must not
    # claim an application was sent, so it stops here and says so plainly.
    raise ConflictError(
        "The approval checks passed, but the browser submission step is not "
        "implemented yet (Phase 5 and 6). Nothing was sent.",
        code="SUBMISSION_NOT_IMPLEMENTED",
        details={"phase": 6},
    )


async def cancel(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    reason: str | None = None,
) -> Application:
    application = await get_application(session, user_id=user_id, application_id=application_id)
    application.cancelled_at = utcnow()
    for approval in application.approvals:
        if approval.revoked_at is None:
            approval.revoked_at = utcnow()
            approval.revoked_reason = "The application was cancelled."
    return await transition(
        session,
        application=application,
        to_state=ApplicationState.CANCELLED,
        message=reason or "You cancelled this application.",
        actor_user_id=user_id,
    )


async def set_outcome(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    outcome: ApplicationOutcome,
    note: str | None = None,
) -> Application:
    """Record what the employer did. Always a user action, never inferred."""
    application = await get_application(session, user_id=user_id, application_id=application_id)
    application.outcome = outcome
    application.outcome_note = note
    application.outcome_updated_at = utcnow()
    await record_event(
        session,
        application=application,
        event_type="outcome_updated",
        message=f"You recorded the outcome as {outcome.value}.",
        actor_user_id=user_id,
        payload={"outcome": outcome.value},
    )
    return application


async def record_manual_submission(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    note: str | None = None,
) -> Application:
    """Mark an application the user submitted themselves, in their own browser.

    This exists because it is the honest path while the browser agent is still being
    built: CareerPilot drafts the answers, the person applies, and the tracker records
    it. ``submission_evidence`` says explicitly that the evidence is the user's word,
    so a manual submission is never confused with one CareerPilot verified.
    """
    application = await get_application(session, user_id=user_id, application_id=application_id)
    if application.already_submitted:
        raise ConflictError(
            "This application is already recorded as submitted.",
            code="ALREADY_SUBMITTED",
        )
    if application.state in (ApplicationState.SUBMITTED, ApplicationState.CANCELLED):
        raise ConflictError(
            f"An application in {application.state.value} cannot be marked as submitted.",
            code="INVALID_STATE_TRANSITION",
        )

    now = utcnow()
    application.submission_attempted_at = now
    application.submission_idempotency_key = secrets.token_hex(16)
    application.submission_evidence = {
        "source": "user_reported",
        "note": note,
        "verified_by_careerpilot": False,
    }
    application.unresolved_interventions = []

    # Walk the legal path to SUBMITTED rather than writing the state directly, so the
    # transition map stays the single source of truth about which moves exist and the
    # event history stays complete.
    from app.domain.states import shortest_path

    path = shortest_path(application.state, ApplicationState.SUBMITTED)
    if path is None:
        raise ConflictError(
            f"There is no legal path from {application.state.value} to SUBMITTED.",
            code="INVALID_STATE_TRANSITION",
            details={"current_state": application.state.value},
        )

    for index, step in enumerate(path):
        is_final = index == len(path) - 1
        if is_final:
            # `submitted_at` is set together with the SUBMITTED write, in the same
            # flush. Setting it any earlier would violate
            # ck_applications_submitted_at_requires_submitted_state the moment an
            # intermediate transition (e.g. SELECTED -> PREPARING) flushes.
            application.submitted_at = now
        await transition(
            session,
            application=application,
            to_state=step,
            event_type="submitted_manually" if is_final else "manual_submission_progress",
            message=(
                "You recorded that you submitted this application yourself."
                if is_final
                else "Recording a submission you made yourself."
            ),
            actor_user_id=user_id,
            payload={"verified_by_careerpilot": False} if is_final else {},
        )
    return application
