"""The submission gate.

This is the one function that decides whether a real application may be sent to a real
employer. Everything about its design follows from that.

* It is **pure**. No database, no HTTP, no clock, no model. ``now`` is a parameter.
  That means every branch is unit-testable with no infrastructure, and it is, in
  ``tests/unit/test_submission_gate.py``.
* It is **closed by default**. The result starts as refused and every condition must
  actively clear. A field the caller forgot to populate produces a blocker, not a pass.
* It returns **all** blockers, not the first. The user should see everything standing
  between them and submitting, in one review.
* **No model can reach it, and no model output is an input to it.** The inputs are
  ownership, an approval record, a verification result, and field states. A language
  model has no say in any of them.

The gate is evaluated twice: once when rendering the review screen, so the UI can
explain what is outstanding, and again inside the transaction that performs the
submission. The second call is the one that matters. The first is a courtesy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from app.domain.approval import (
    ApprovalRecord,
    FieldResolution,
    FormSnapshot,
    VerificationStatus,
    approval_matches,
)
from app.domain.states import ApplicationState, InterventionReason


class BlockerCode(StrEnum):
    """Why a submission is refused. Stable strings; clients branch on these."""

    NOT_OWNER = "NOT_OWNER"
    NO_APPROVAL = "NO_APPROVAL"
    APPROVAL_REVOKED = "APPROVAL_REVOKED"
    APPROVAL_STALE = "APPROVAL_STALE"
    APPROVAL_NOT_FOR_THIS_APPLICATION = "APPROVAL_NOT_FOR_THIS_APPLICATION"
    NO_VERIFICATION = "NO_VERIFICATION"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    VERIFICATION_INCONCLUSIVE = "VERIFICATION_INCONCLUSIVE"
    VERIFICATION_STALE = "VERIFICATION_STALE"
    UNRESOLVED_REQUIRED_FIELDS = "UNRESOLVED_REQUIRED_FIELDS"
    FIELDS_NEED_HUMAN = "FIELDS_NEED_HUMAN"
    UNKNOWN_FIELDS_PRESENT = "UNKNOWN_FIELDS_PRESENT"
    UNRESOLVED_INTERVENTION = "UNRESOLVED_INTERVENTION"
    WRONG_STATE = "WRONG_STATE"
    ALREADY_SUBMITTED = "ALREADY_SUBMITTED"
    JOB_CLOSED = "JOB_CLOSED"
    NO_FORM_STATE = "NO_FORM_STATE"
    NOT_HUMAN_INITIATED = "NOT_HUMAN_INITIATED"


@dataclass(frozen=True, slots=True)
class Blocker:
    code: BlockerCode
    message: str
    detail: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"code": self.code.value, "message": self.message, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class SubmissionContext:
    """Everything the gate is allowed to consider.

    Assembled by the application service from database rows immediately before the
    decision. Nothing here is derived from model output.
    """

    application_id: str
    owner_user_id: str
    #: The authenticated user making the request. Compared, not trusted.
    requesting_user_id: str
    state: ApplicationState
    #: Hash of the form as it stands right now, recomputed from current field values.
    current_form_state_hash: str | None
    approval: ApprovalRecord | None
    #: Result of the most recent read-back verification of the live page.
    verification_status: VerificationStatus
    verified_at: datetime | None
    form_snapshot: FormSnapshot | None
    unresolved_interventions: tuple[InterventionReason, ...] = field(default_factory=tuple)
    #: True once a submission has been recorded, however it turned out.
    already_submitted: bool = False
    job_closed: bool = False
    #: False for any non-interactive caller. A scheduled task or worker must never
    #: be able to satisfy the gate, regardless of whose token it holds.
    human_initiated: bool = True


@dataclass(frozen=True, slots=True)
class SubmissionDecision:
    allowed: bool
    blockers: tuple[Blocker, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "blockers": [b.as_dict() for b in self.blockers],
        }

    @property
    def codes(self) -> tuple[BlockerCode, ...]:
        return tuple(b.code for b in self.blockers)


#: Verification older than this is not trusted: the page may have changed, the session
#: may have expired, the posting may have closed. Overridable per call.
DEFAULT_MAX_VERIFICATION_AGE: Final = timedelta(minutes=15)

#: The only state from which a submission may proceed.
REQUIRED_STATE: Final = ApplicationState.WAITING_FOR_APPROVAL


def evaluate(
    context: SubmissionContext,
    *,
    now: datetime,
    max_verification_age: timedelta = DEFAULT_MAX_VERIFICATION_AGE,
) -> SubmissionDecision:
    """Decide whether this application may be submitted right now.

    Returns every reason it may not. An empty blocker list is the only thing that
    permits a submission, and the caller must treat anything else as a refusal.
    """
    if now.tzinfo is None:
        raise ValueError("`now` must be timezone-aware; the gate compares absolute times.")

    blockers: list[Blocker] = []

    # 1. Ownership. Checked against the row, never inferred from the request.
    if context.requesting_user_id != context.owner_user_id:
        blockers.append(
            Blocker(
                code=BlockerCode.NOT_OWNER,
                message="You do not own this application.",
            )
        )

    # A non-interactive caller is refused outright. This closes the door on a worker,
    # a cron job, or an agent loop ever satisfying the gate by holding a valid token.
    if not context.human_initiated:
        blockers.append(
            Blocker(
                code=BlockerCode.NOT_HUMAN_INITIATED,
                message=(
                    "Submission must be triggered by an explicit action from the signed-in "
                    "user. Automated callers cannot submit applications."
                ),
            )
        )

    # 2. State. SUBMITTING is reachable from nowhere else in the state machine, so
    # this check and that map reinforce each other.
    if context.state is not REQUIRED_STATE:
        blockers.append(
            Blocker(
                code=BlockerCode.WRONG_STATE,
                message=(
                    f"An application must be in {REQUIRED_STATE.value} to be submitted; "
                    f"this one is in {context.state.value}."
                ),
                detail=context.state.value,
            )
        )

    # 3. Idempotency. A potentially successful submission is never repeated.
    if context.already_submitted:
        blockers.append(
            Blocker(
                code=BlockerCode.ALREADY_SUBMITTED,
                message=(
                    "A submission has already been recorded for this application. If its "
                    "outcome is uncertain, reconcile it rather than submitting again."
                ),
            )
        )

    # 4. The job must still be open.
    if context.job_closed:
        blockers.append(
            Blocker(
                code=BlockerCode.JOB_CLOSED,
                message="This posting is closed, so there is nothing to submit to.",
            )
        )

    # 5. There has to be a form state to approve in the first place.
    if context.current_form_state_hash is None:
        blockers.append(
            Blocker(
                code=BlockerCode.NO_FORM_STATE,
                message=(
                    "The application form has not been filled and captured yet, so there "
                    "is nothing to submit."
                ),
            )
        )

    # 6. Approval, bound to this application and to this exact form state.
    approval = context.approval
    if approval is None:
        blockers.append(
            Blocker(
                code=BlockerCode.NO_APPROVAL,
                message="You have not approved this application for submission.",
            )
        )
    else:
        if approval.application_id != context.application_id:
            blockers.append(
                Blocker(
                    code=BlockerCode.APPROVAL_NOT_FOR_THIS_APPLICATION,
                    message="The approval on file belongs to a different application.",
                )
            )
        if not approval.is_active:
            blockers.append(
                Blocker(
                    code=BlockerCode.APPROVAL_REVOKED,
                    message="The approval for this application was revoked.",
                )
            )
        if approval.approved_by_user_id != context.owner_user_id:
            blockers.append(
                Blocker(
                    code=BlockerCode.NOT_OWNER,
                    message="The approval was not given by the owner of this application.",
                )
            )
        if context.current_form_state_hash is not None and not approval_matches(
            approval, current_form_state_hash=context.current_form_state_hash
        ):
            blockers.append(
                Blocker(
                    code=BlockerCode.APPROVAL_STALE,
                    message=(
                        "The form has changed since you approved it. Review the current "
                        "version and approve again."
                    ),
                    detail=(
                        f"approved={approval.form_state_hash[:12]} "
                        f"current={context.current_form_state_hash[:12]}"
                    ),
                )
            )

    # 7. Verification: present, passed, and fresh.
    if (
        context.verification_status is VerificationStatus.NOT_ATTEMPTED
        or context.verified_at is None
    ):
        blockers.append(
            Blocker(
                code=BlockerCode.NO_VERIFICATION,
                message=(
                    "The filled form has not been verified against the live page. "
                    "Nothing is submitted on an unverified form."
                ),
            )
        )
    else:
        if context.verification_status is VerificationStatus.FAILED:
            blockers.append(
                Blocker(
                    code=BlockerCode.VERIFICATION_FAILED,
                    message=(
                        "Verification found at least one field on the page that does not "
                        "hold the intended value."
                    ),
                )
            )
        elif context.verification_status is VerificationStatus.INCONCLUSIVE:
            blockers.append(
                Blocker(
                    code=BlockerCode.VERIFICATION_INCONCLUSIVE,
                    message=(
                        "Verification could not confirm the form contents. A person needs "
                        "to check the page."
                    ),
                )
            )
        age = now - context.verified_at
        if age > max_verification_age:
            blockers.append(
                Blocker(
                    code=BlockerCode.VERIFICATION_STALE,
                    message=(
                        "The last verification is too old to rely on. Re-verify the form "
                        "before submitting."
                    ),
                    detail=(
                        f"age={int(age.total_seconds())}s "
                        f"limit={int(max_verification_age.total_seconds())}s"
                    ),
                )
            )
        elif age < -timedelta(minutes=5):
            # A verification timestamp in the future means clock skew or a bad write.
            # Refusing is the only safe reading.
            blockers.append(
                Blocker(
                    code=BlockerCode.VERIFICATION_STALE,
                    message="The verification timestamp is in the future and cannot be trusted.",
                    detail=f"verified_at={context.verified_at.isoformat()}",
                )
            )

    # 8. Fields: nothing outstanding, nothing waiting on a human, nothing unrecognised.
    snapshot = context.form_snapshot
    if snapshot is None:
        if context.current_form_state_hash is not None:
            blockers.append(
                Blocker(
                    code=BlockerCode.UNRESOLVED_REQUIRED_FIELDS,
                    message="The form contents could not be read for review.",
                )
            )
    else:
        needs_human = [f for f in snapshot.fields if f.resolution is FieldResolution.NEEDS_HUMAN]
        unknown = [f for f in snapshot.fields if f.resolution is FieldResolution.UNKNOWN_FIELD]
        unresolved_required = [
            f
            for f in snapshot.fields
            if f.required
            and f.resolution not in (FieldResolution.NEEDS_HUMAN, FieldResolution.UNKNOWN_FIELD)
            and f.is_blocking
        ]
        if needs_human:
            blockers.append(
                Blocker(
                    code=BlockerCode.FIELDS_NEED_HUMAN,
                    message=f"{len(needs_human)} field(s) need an answer from you.",
                    detail=", ".join(f.label or f.field_id for f in needs_human[:10]),
                )
            )
        if unknown:
            blockers.append(
                Blocker(
                    code=BlockerCode.UNKNOWN_FIELDS_PRESENT,
                    message=(
                        f"{len(unknown)} field(s) on the page were not recognised. "
                        "They must be resolved by hand."
                    ),
                    detail=", ".join(f.label or f.field_id for f in unknown[:10]),
                )
            )
        if unresolved_required:
            blockers.append(
                Blocker(
                    code=BlockerCode.UNRESOLVED_REQUIRED_FIELDS,
                    message=(
                        f"{len(unresolved_required)} required field(s) are not filled and verified."
                    ),
                    detail=", ".join(f.label or f.field_id for f in unresolved_required[:10]),
                )
            )

    # 9. No outstanding intervention. A pending CAPTCHA or login is a hard stop.
    if context.unresolved_interventions:
        reasons = ", ".join(r.value for r in context.unresolved_interventions)
        blockers.append(
            Blocker(
                code=BlockerCode.UNRESOLVED_INTERVENTION,
                message=f"This application is waiting on you: {reasons}.",
                detail=reasons,
            )
        )

    # Deduplicate while preserving order; NOT_OWNER can be reached twice.
    deduplicated: list[Blocker] = []
    seen: set[tuple[BlockerCode, str | None]] = set()
    for blocker in blockers:
        key = (blocker.code, blocker.detail)
        if key not in seen:
            seen.add(key)
            deduplicated.append(blocker)

    return SubmissionDecision(allowed=not deduplicated, blockers=tuple(deduplicated))


def assert_submittable(
    context: SubmissionContext,
    *,
    now: datetime,
    max_verification_age: timedelta = DEFAULT_MAX_VERIFICATION_AGE,
) -> None:
    """Raise :class:`SubmissionBlockedError` unless the gate permits submission.

    The API layer calls this. Importing the error type here is the one concession the
    domain makes to the rest of the application, and ``app.core.errors`` is itself
    pure Python with no framework dependency.
    """
    from app.core.errors import SubmissionBlockedError

    decision = evaluate(context, now=now, max_verification_age=max_verification_age)
    if not decision.allowed:
        raise SubmissionBlockedError(
            "This application cannot be submitted yet.",
            details={"blockers": [b.as_dict() for b in decision.blockers]},
        )
