"""The submission gate.

This is the most important test module in the project. It is the standing guard on
safety requirements 1, 2, 3, 5 and 11: nothing reaches an employer without an explicit,
current, human approval of a verified form.

Every test here runs with no database, no network, and no running services, because
``app.domain.submission_gate.evaluate`` is a pure function over dataclasses. That is
the point of keeping it pure: the guarantee can always be checked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import SubmissionBlockedError
from app.domain.approval import (
    ApprovalRecord,
    DocumentSnapshot,
    FieldResolution,
    FieldSnapshot,
    FormSnapshot,
    VerificationStatus,
)
from app.domain.states import ApplicationState, InterventionReason
from app.domain.submission_gate import (
    BlockerCode,
    SubmissionContext,
    assert_submittable,
    evaluate,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
OWNER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
APPLICATION = "33333333-3333-3333-3333-333333333333"
FORM_HASH = "a" * 64


def _snapshot(*, needs_human: bool = False, unknown: bool = False) -> FormSnapshot:
    fields = [
        FieldSnapshot(
            field_id="full_name",
            label="Full name",
            resolution=FieldResolution.RESOLVED,
            required=True,
            value_for_hash="Demo Candidate",
            verification=VerificationStatus.PASSED,
        ),
        FieldSnapshot(
            field_id="email",
            label="Email",
            resolution=FieldResolution.RESOLVED,
            required=True,
            value_for_hash="demo@example.com",
            verification=VerificationStatus.PASSED,
        ),
    ]
    if needs_human:
        fields.append(
            FieldSnapshot(
                field_id="work_authorisation",
                label="Are you authorised to work here?",
                resolution=FieldResolution.NEEDS_HUMAN,
                required=True,
                value_for_hash=None,
            )
        )
    if unknown:
        fields.append(
            FieldSnapshot(
                field_id="mystery_question",
                label="Unrecognised question",
                resolution=FieldResolution.UNKNOWN_FIELD,
                required=False,
                value_for_hash=None,
            )
        )
    return FormSnapshot(
        application_url="https://example.invalid/apply",
        fields=tuple(fields),
        documents=(DocumentSnapshot(slot="resume", filename="cv.pdf", content_sha256="b" * 64),),
    )


def _approval(
    *,
    form_hash: str = FORM_HASH,
    by: str = OWNER,
    application_id: str = APPLICATION,
    revoked: bool = False,
) -> ApprovalRecord:
    return ApprovalRecord(
        application_id=application_id,
        approved_by_user_id=by,
        approved_at=NOW - timedelta(minutes=1),
        form_state_hash=form_hash,
        revoked_at=NOW if revoked else None,
    )


def _context(**overrides: object) -> SubmissionContext:
    """A context that would be *allowed*, so each test can break exactly one thing."""
    defaults: dict[str, object] = {
        "application_id": APPLICATION,
        "owner_user_id": OWNER,
        "requesting_user_id": OWNER,
        "state": ApplicationState.WAITING_FOR_APPROVAL,
        "current_form_state_hash": FORM_HASH,
        "approval": _approval(),
        "verification_status": VerificationStatus.PASSED,
        "verified_at": NOW - timedelta(seconds=30),
        "form_snapshot": _snapshot(),
        "unresolved_interventions": (),
        "already_submitted": False,
        "job_closed": False,
        "human_initiated": True,
    }
    defaults.update(overrides)
    return SubmissionContext(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The one allowed case
# ---------------------------------------------------------------------------


def test_fully_satisfied_context_is_allowed() -> None:
    """The happy path, so the rest of the module is testing real refusals."""
    decision = evaluate(_context(), now=NOW)
    assert decision.allowed is True
    assert decision.blockers == ()


# ---------------------------------------------------------------------------
# Each condition, broken individually
# ---------------------------------------------------------------------------


def test_non_owner_is_refused() -> None:
    decision = evaluate(_context(requesting_user_id=OTHER), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.NOT_OWNER in decision.codes


def test_missing_approval_is_refused() -> None:
    decision = evaluate(_context(approval=None), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.NO_APPROVAL in decision.codes


def test_revoked_approval_is_refused() -> None:
    decision = evaluate(_context(approval=_approval(revoked=True)), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.APPROVAL_REVOKED in decision.codes


def test_approval_for_a_different_form_state_is_refused() -> None:
    """Safety requirement 3.

    The user approved one version of the form; the form now differs. Their approval
    does not carry over, and the gate says so specifically rather than generically.
    """
    decision = evaluate(_context(approval=_approval(form_hash="c" * 64)), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.APPROVAL_STALE in decision.codes


def test_approval_belonging_to_another_application_is_refused() -> None:
    other_application = "44444444-4444-4444-4444-444444444444"
    decision = evaluate(_context(approval=_approval(application_id=other_application)), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.APPROVAL_NOT_FOR_THIS_APPLICATION in decision.codes


def test_approval_by_someone_other_than_the_owner_is_refused() -> None:
    decision = evaluate(_context(approval=_approval(by=OTHER)), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.NOT_OWNER in decision.codes


def test_unverified_form_is_refused() -> None:
    decision = evaluate(
        _context(verification_status=VerificationStatus.NOT_ATTEMPTED, verified_at=None),
        now=NOW,
    )
    assert decision.allowed is False
    assert BlockerCode.NO_VERIFICATION in decision.codes


def test_failed_verification_is_refused() -> None:
    decision = evaluate(_context(verification_status=VerificationStatus.FAILED), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.VERIFICATION_FAILED in decision.codes


def test_inconclusive_verification_is_refused() -> None:
    """An action that returned successfully is not evidence the page holds the value."""
    decision = evaluate(_context(verification_status=VerificationStatus.INCONCLUSIVE), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.VERIFICATION_INCONCLUSIVE in decision.codes


def test_stale_verification_is_refused() -> None:
    decision = evaluate(
        _context(verified_at=NOW - timedelta(hours=2)),
        now=NOW,
        max_verification_age=timedelta(minutes=15),
    )
    assert decision.allowed is False
    assert BlockerCode.VERIFICATION_STALE in decision.codes


def test_verification_timestamp_in_the_future_is_refused() -> None:
    """Clock skew or a bad write. Refusing is the only safe reading."""
    decision = evaluate(_context(verified_at=NOW + timedelta(hours=1)), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.VERIFICATION_STALE in decision.codes


def test_fields_needing_a_human_are_refused() -> None:
    """Safety requirement 5: an unknown answer is never guessed."""
    decision = evaluate(_context(form_snapshot=_snapshot(needs_human=True)), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.FIELDS_NEED_HUMAN in decision.codes


def test_unrecognised_fields_are_refused() -> None:
    """An unsupported form layout fails safe rather than being guessed at."""
    decision = evaluate(_context(form_snapshot=_snapshot(unknown=True)), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.UNKNOWN_FIELDS_PRESENT in decision.codes


def test_required_field_that_is_not_verified_is_refused() -> None:
    snapshot = FormSnapshot(
        application_url="https://example.invalid/apply",
        fields=(
            FieldSnapshot(
                field_id="full_name",
                label="Full name",
                resolution=FieldResolution.RESOLVED,
                required=True,
                value_for_hash="Demo Candidate",
                # Filled, but never read back off the page.
                verification=VerificationStatus.NOT_ATTEMPTED,
            ),
        ),
    )
    decision = evaluate(_context(form_snapshot=snapshot), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.UNRESOLVED_REQUIRED_FIELDS in decision.codes


@pytest.mark.parametrize(
    "reason",
    [
        InterventionReason.NEEDS_CAPTCHA,
        InterventionReason.NEEDS_MFA,
        InterventionReason.NEEDS_LOGIN,
        InterventionReason.NEEDS_HUMAN,
    ],
)
def test_unresolved_intervention_is_refused(reason: InterventionReason) -> None:
    """Safety requirement 6: a challenge is a stop, not an obstacle to engineer around."""
    decision = evaluate(_context(unresolved_interventions=(reason,)), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.UNRESOLVED_INTERVENTION in decision.codes


@pytest.mark.parametrize(
    "state",
    [
        ApplicationState.DISCOVERED,
        ApplicationState.MATCHED,
        ApplicationState.SELECTED,
        ApplicationState.PREPARING,
        ApplicationState.READY,
        ApplicationState.FILLING,
        ApplicationState.VERIFYING,
        ApplicationState.WAITING_FOR_USER_INPUT,
        ApplicationState.SUBMITTING,
        ApplicationState.SUBMITTED,
        ApplicationState.SUBMITTED_UNCERTAIN,
        ApplicationState.FAILED,
        ApplicationState.CANCELLED,
    ],
)
def test_only_waiting_for_approval_may_submit(state: ApplicationState) -> None:
    """Every state except ``WAITING_FOR_APPROVAL`` is refused.

    Parametrised over the whole enum, so adding a new state without deciding whether
    it may submit makes this test fail rather than silently permitting it.
    """
    decision = evaluate(_context(state=state), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.WRONG_STATE in decision.codes


def test_already_submitted_is_refused() -> None:
    """Safety requirement 11: a potentially successful submission is never repeated."""
    decision = evaluate(_context(already_submitted=True), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.ALREADY_SUBMITTED in decision.codes


def test_closed_job_is_refused() -> None:
    decision = evaluate(_context(job_closed=True), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.JOB_CLOSED in decision.codes


def test_missing_form_state_is_refused() -> None:
    decision = evaluate(_context(current_form_state_hash=None), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.NO_FORM_STATE in decision.codes


def test_non_human_caller_is_refused_even_when_everything_else_passes() -> None:
    """Safety requirement 1, stated directly.

    A worker, a scheduled task, or an agent loop holding a valid token still cannot
    submit. Note that every other condition in this context is satisfied: the *only*
    thing wrong is that no human pressed the button.
    """
    decision = evaluate(_context(human_initiated=False), now=NOW)
    assert decision.allowed is False
    assert BlockerCode.NOT_HUMAN_INITIATED in decision.codes


# ---------------------------------------------------------------------------
# Behaviour of the decision itself
# ---------------------------------------------------------------------------


def test_all_blockers_are_reported_not_just_the_first() -> None:
    """The review screen should show everything outstanding in one pass."""
    decision = evaluate(
        _context(
            requesting_user_id=OTHER,
            approval=None,
            verification_status=VerificationStatus.NOT_ATTEMPTED,
            verified_at=None,
            state=ApplicationState.READY,
            form_snapshot=_snapshot(needs_human=True),
            unresolved_interventions=(InterventionReason.NEEDS_CAPTCHA,),
        ),
        now=NOW,
    )
    assert decision.allowed is False
    expected = {
        BlockerCode.NOT_OWNER,
        BlockerCode.NO_APPROVAL,
        BlockerCode.NO_VERIFICATION,
        BlockerCode.WRONG_STATE,
        BlockerCode.FIELDS_NEED_HUMAN,
        BlockerCode.UNRESOLVED_INTERVENTION,
    }
    assert expected.issubset(set(decision.codes))


def test_blockers_are_deduplicated() -> None:
    """NOT_OWNER is reachable twice; the user should not see it twice."""
    decision = evaluate(_context(requesting_user_id=OTHER, approval=_approval(by=OTHER)), now=NOW)
    owner_blockers = [c for c in decision.codes if c is BlockerCode.NOT_OWNER]
    assert len(owner_blockers) == 1


def test_naive_now_is_rejected() -> None:
    """A naive datetime would make the freshness comparison meaningless."""
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate(_context(), now=datetime(2026, 6, 15, 12, 0))


def test_assert_submittable_raises_with_the_blocker_list() -> None:
    """The API relies on the blockers reaching the client inside the 409 body."""
    with pytest.raises(SubmissionBlockedError) as caught:
        assert_submittable(_context(approval=None), now=NOW)

    details = caught.value.details
    assert isinstance(details, dict)
    codes = {blocker["code"] for blocker in details["blockers"]}
    assert BlockerCode.NO_APPROVAL.value in codes


def test_assert_submittable_passes_a_satisfied_context() -> None:
    assert_submittable(_context(), now=NOW)  # must not raise


def test_decision_serialises_for_the_api() -> None:
    payload = evaluate(_context(approval=None), now=NOW).as_dict()
    assert payload["allowed"] is False
    assert isinstance(payload["blockers"], list)
    assert {"code", "message", "detail"} <= set(payload["blockers"][0])


def test_default_context_requires_explicit_opt_in_for_every_condition() -> None:
    """A context built with only the mandatory fields must be refused.

    The gate is closed by default: a caller that forgets to populate something gets a
    refusal, never an accidental pass.
    """
    bare = SubmissionContext(
        application_id=APPLICATION,
        owner_user_id=OWNER,
        requesting_user_id=OWNER,
        state=ApplicationState.SELECTED,
        current_form_state_hash=None,
        approval=None,
        verification_status=VerificationStatus.NOT_ATTEMPTED,
        verified_at=None,
        form_snapshot=None,
    )
    decision = evaluate(bare, now=NOW)
    assert decision.allowed is False
    assert BlockerCode.NO_APPROVAL in decision.codes
    assert BlockerCode.NO_VERIFICATION in decision.codes
    assert BlockerCode.NO_FORM_STATE in decision.codes
