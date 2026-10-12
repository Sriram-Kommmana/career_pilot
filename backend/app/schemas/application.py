"""Application contracts, including the review payload and the gate's decision.

:class:`ApplicationFieldOut` is the audit row a user reads before approving. It carries
``value_provenance`` next to every value, so "where did this come from?" is answerable
without leaving the screen, and sensitive values appear masked.

:class:`SubmissionDecisionOut` is the gate's verdict. It is returned by the review
endpoint *and* embedded in the 409 body when a submission is refused, so the client
renders the same blocker list either way.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field

from app.domain.approval import FieldResolution, VerificationStatus
from app.domain.provenance import ProvenanceKind, SensitiveCategory
from app.domain.redaction import Sensitivity
from app.domain.states import ApplicationOutcome, ApplicationState
from app.schemas.common import ApiModel, ApiRequest
from app.schemas.job import JobSummaryOut


class ApplicationFieldOut(ApiModel):
    id: uuid.UUID
    field_key: str
    label: str | None
    field_type: str
    is_required: bool
    resolution: FieldResolution
    sensitivity: Sensitivity
    #: Masked for anything sensitive. Passwords and session state are never stored.
    value_masked: str | None
    #: Where the value came from. ``UNKNOWN`` means it cannot be used.
    value_provenance: ProvenanceKind
    sensitive_category: SensitiveCategory | None
    action_result: str | None
    verification: VerificationStatus
    verification_note: str | None
    verified_at: datetime | None
    needs_human: bool


class ApplicationAnswerOut(ApiModel):
    id: uuid.UUID
    question_key: str
    question_text: str
    answer_text: str | None
    provenance: ProvenanceKind
    evidence_refs: list[dict[str, object]]
    #: True for model-written prose. The UI must present it visibly as a draft, and it
    #: can never satisfy a required field.
    is_generated: bool
    sensitive_category: SensitiveCategory | None
    needs_human: bool
    user_edited_at: datetime | None


class ApplicationEventOut(ApiModel):
    id: uuid.UUID
    occurred_at: datetime
    event_type: str
    from_state: ApplicationState | None
    to_state: ApplicationState | None
    intervention_reason: str | None
    #: A short, user-facing sentence. Never internal reasoning.
    message: str | None
    correlation_id: str | None


class ApprovalOut(ApiModel):
    id: uuid.UUID
    approved_at: datetime
    #: The form state this approval covers. A different current hash voids it.
    form_state_hash: str
    note: str | None
    revoked_at: datetime | None
    revoked_reason: str | None


class ApplicationSummaryOut(ApiModel):
    id: uuid.UUID
    job: JobSummaryOut
    state: ApplicationState
    outcome: ApplicationOutcome
    outcome_note: str | None
    application_url: str
    verification_status: VerificationStatus
    verified_at: datetime | None
    unresolved_interventions: list[str]
    submitted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApplicationDetailOut(ApplicationSummaryOut):
    form_state_hash: str | None
    resume_id: uuid.UUID | None
    submission_attempted_at: datetime | None
    submission_evidence: dict[str, object] | None
    failure_reason: str | None
    fields: list[ApplicationFieldOut] = Field(default_factory=list)
    answers: list[ApplicationAnswerOut] = Field(default_factory=list)
    events: list[ApplicationEventOut] = Field(default_factory=list)
    approvals: list[ApprovalOut] = Field(default_factory=list)


class BlockerOut(ApiModel):
    code: str
    message: str
    detail: str | None = None


class SubmissionDecisionOut(ApiModel):
    """The gate's verdict.

    ``allowed`` is true only when ``blockers`` is empty. A client must treat any
    non-empty blocker list as a refusal, and the backend enforces that regardless.
    """

    allowed: bool
    blockers: list[BlockerOut] = Field(default_factory=list)


class ApplicationReviewOut(ApiModel):
    """Everything the review screen shows before a user approves.

    Deliberately complete: job and company, the exact URL, every field with its value
    and source, the attached documents, what is unresolved, the verification result,
    and when it was verified.
    """

    application: ApplicationDetailOut
    decision: SubmissionDecisionOut
    unresolved_field_labels: list[str] = Field(default_factory=list)
    documents: list[dict[str, object]] = Field(default_factory=list)
    last_verified_at: datetime | None = None


class CreateApplicationRequest(ApiRequest):
    job_id: uuid.UUID
    resume_id: uuid.UUID | None = None


class AnswerFieldRequest(ApiRequest):
    field_key: Annotated[str, Field(min_length=1, max_length=300)]
    value: Annotated[str, Field(min_length=1, max_length=20000)]


class ApproveRequest(ApiRequest):
    """Explicit approval of the current form state.

    ``form_state_hash`` is required and must equal the server's current hash. This is
    not redundant with the server-side check: it proves the client approved the state
    it actually displayed, so an approval cannot be submitted against a form that
    changed while the user was reading it.
    """

    form_state_hash: Annotated[str, Field(min_length=64, max_length=64)]
    note: Annotated[str | None, Field(max_length=1000)] = None


class SubmitRequest(ApiRequest):
    """Explicit confirmation for one specific application.

    ``confirm`` must be ``True``. A bare POST cannot submit an application, so a
    mis-wired client or a replayed request does not become a real submission.
    """

    confirm: bool = Field(description="Must be true. An explicit human confirmation.")
    form_state_hash: Annotated[str, Field(min_length=64, max_length=64)]


class CancelRequest(ApiRequest):
    reason: Annotated[str | None, Field(max_length=500)] = None


class OutcomeRequest(ApiRequest):
    outcome: ApplicationOutcome
    note: Annotated[str | None, Field(max_length=1000)] = None


class ManualSubmissionRequest(ApiRequest):
    """Record a submission the user made themselves, in their own browser.

    ``confirm`` is required because this writes a submitted state the product cannot
    verify. The stored evidence says so explicitly.
    """

    confirm: bool
    note: Annotated[str | None, Field(max_length=1000)] = None


class TrackerBoardOut(ApiModel):
    columns: dict[str, list[ApplicationSummaryOut]]
    column_order: list[str]
