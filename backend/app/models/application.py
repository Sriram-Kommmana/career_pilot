"""Applications, their fields, answers, events, and approvals.

This is where the safety guarantees become database constraints rather than good
intentions:

* A **partial unique index** on ``(user_id, job_id)`` for non-terminal states makes a
  duplicate live application impossible, even under a race between two requests.
* ``submission_idempotency_key`` is unique, so a double-clicked submit button cannot
  become two submissions.
* ``application_answers.provenance`` is ``NOT NULL`` — an answer cannot be stored
  without recording where it came from.
* ``approval_records.form_state_hash`` binds consent to the exact reviewed state.
* ``application_events`` is append-only history; nothing updates a row in it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column
from app.domain.approval import FieldResolution, VerificationStatus
from app.domain.provenance import ProvenanceKind, SensitiveCategory
from app.domain.redaction import Sensitivity
from app.domain.states import ApplicationOutcome, ApplicationState, InterventionReason

if TYPE_CHECKING:
    from app.models.job import Job


class Application(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "applications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True
    )

    state: Mapped[ApplicationState] = mapped_column(
        enum_column(ApplicationState, name="application_state"),
        nullable=False,
        default=ApplicationState.SELECTED,
        index=True,
    )
    #: What the employer did, set by the user. Separate from the automation lifecycle.
    outcome: Mapped[ApplicationOutcome] = mapped_column(
        enum_column(ApplicationOutcome, name="application_outcome"),
        nullable=False,
        default=ApplicationOutcome.NONE,
    )
    outcome_note: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    outcome_updated_at: Mapped[datetime | None] = mapped_column(nullable=True)

    #: The URL actually used. Copied from the job at preparation time so a later
    #: change to the posting cannot silently redirect a reviewed application.
    application_url: Mapped[str] = mapped_column(sa.String(1000), nullable=False)

    #: Digest of the current form state, from ``domain.approval.compute_form_state_hash``.
    #: NULL until a form has been captured. The submission gate refuses on NULL.
    form_state_hash: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)

    verification_status: Mapped[VerificationStatus] = mapped_column(
        enum_column(VerificationStatus, name="application_verification_status"),
        nullable=False,
        default=VerificationStatus.NOT_ATTEMPTED,
    )
    verified_at: Mapped[datetime | None] = mapped_column(nullable=True)

    #: Open intervention reasons. Any entry here blocks submission.
    unresolved_interventions: Mapped[list[str]] = mapped_column(nullable=False, default=list)

    #: Set once a submission has been *attempted*, before the result is known, so a
    #: crash mid-submit still leaves evidence that something may have been sent.
    submission_attempted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: Why we believe the submission succeeded: confirmation text, URL, screenshot path.
    submission_evidence: Mapped[dict[str, object] | None] = mapped_column(nullable=True)
    #: Claimed atomically in the submit transaction. Unique, so a retry is a no-op.
    submission_idempotency_key: Mapped[str | None] = mapped_column(
        sa.String(64), nullable=True, unique=True
    )

    failure_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Eager-loaded: an application is never rendered without its job.
    job: Mapped[Job] = relationship(lazy="selectin")
    answers: Mapped[list[ApplicationAnswer]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )
    fields: Mapped[list[ApplicationField]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )
    events: Mapped[list[ApplicationEvent]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="ApplicationEvent.occurred_at",
    )
    approvals: Mapped[list[ApprovalRecordRow]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # One live application per job. CANCELLED and FAILED are excluded so a user
        # can legitimately try again after cancelling, but cannot have two in flight.
        sa.Index(
            "uq_applications_live_user_job",
            "user_id",
            "job_id",
            unique=True,
            postgresql_where=sa.text("state NOT IN ('CANCELLED', 'FAILED')"),
        ),
        sa.CheckConstraint(
            "(submitted_at IS NULL) OR (state IN ('SUBMITTED', 'SUBMITTED_UNCERTAIN'))",
            name="submitted_at_requires_submitted_state",
        ),
        sa.Index("ix_applications_user_state", "user_id", "state"),
    )

    @property
    def already_submitted(self) -> bool:
        """True once any submission has been attempted.

        Deliberately keyed on the *attempt*, not on success: a submission whose result
        is unknown must never be retried automatically.
        """
        return self.submission_attempted_at is not None


class ApplicationField(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One form field on the application page, and what happened to it.

    This is the audit trail the user reads before approving. Per safety requirement 7,
    a sensitive value is stored **masked** — ``value_masked`` might hold ``"****3210"``
    — and ``value_sha256`` carries the change-detection digest so the approval hash
    still changes if the real value does, without the real value being stored here.

    ``action_result`` and ``verification`` are separate on purpose. A Playwright fill
    returning without raising sets ``action_result``; only reading the value back off
    the live page sets ``verification`` to ``PASSED``.
    """

    __tablename__ = "application_fields"

    application_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: Stable identifier on the page: a name attribute, an id, or an adapter key.
    field_key: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    label: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    #: "text" | "email" | "tel" | "textarea" | "select" | "radio" | "checkbox" | "file"
    field_type: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="text")
    selector: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    is_required: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    resolution: Mapped[FieldResolution] = mapped_column(
        enum_column(FieldResolution, name="field_resolution"),
        nullable=False,
        default=FieldResolution.NEEDS_HUMAN,
    )
    sensitivity: Mapped[Sensitivity] = mapped_column(
        enum_column(Sensitivity, name="field_sensitivity"),
        nullable=False,
        default=Sensitivity.PUBLIC,
    )
    #: Plain value for non-sensitive fields; masked for sensitive ones. Never a raw
    #: password or session token, which are not stored at all.
    value_masked: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    value_sha256: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)

    #: Where the value came from. An unsourced value cannot be filled.
    value_provenance: Mapped[ProvenanceKind] = mapped_column(
        enum_column(ProvenanceKind, name="field_value_provenance"),
        nullable=False,
        default=ProvenanceKind.UNKNOWN,
    )
    sensitive_category: Mapped[SensitiveCategory | None] = mapped_column(
        enum_column(SensitiveCategory, name="field_sensitive_category"), nullable=True
    )

    action_result: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    action_error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    verification: Mapped[VerificationStatus] = mapped_column(
        enum_column(VerificationStatus, name="field_verification"),
        nullable=False,
        default=VerificationStatus.NOT_ATTEMPTED,
    )
    verification_note: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    needs_human: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)

    application: Mapped[Application] = relationship(back_populates="fields")

    __table_args__ = (
        sa.UniqueConstraint("application_id", "field_key", name="application_id_field_key"),
    )


class ApplicationAnswer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A free-text answer prepared for an application question.

    ``provenance`` is ``NOT NULL`` with no default: the schema itself refuses an
    answer whose source is unrecorded. ``is_generated`` drives the UI's visual
    separation of drafted prose from verified candidate facts.
    """

    __tablename__ = "application_answers"

    application_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_key: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    question_text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    answer_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    provenance: Mapped[ProvenanceKind] = mapped_column(
        enum_column(ProvenanceKind, name="answer_provenance"), nullable=False
    )
    #: Evidence references backing the answer, as produced by ``domain.provenance``.
    evidence_refs: Mapped[list[dict[str, object]]] = mapped_column(nullable=False, default=list)
    #: True for model-written prose. Such an answer can never satisfy a required field.
    is_generated: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    sensitive_category: Mapped[SensitiveCategory | None] = mapped_column(
        enum_column(SensitiveCategory, name="answer_sensitive_category"), nullable=True
    )
    needs_human: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    user_edited_at: Mapped[datetime | None] = mapped_column(nullable=True)

    application: Mapped[Application] = relationship(back_populates="answers")

    __table_args__ = (
        sa.UniqueConstraint("application_id", "question_key", name="application_id_question_key"),
    )


class ApplicationEvent(UUIDPrimaryKeyMixin, Base):
    """Append-only history for one application.

    No ``updated_at``, because nothing here is ever updated. Every state change,
    intervention, verification result, approval, and failure lands as a row, which is
    what makes "when did this become approved, and against what?" answerable.
    """

    __tablename__ = "application_events"

    application_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(
        server_default=sa.func.now(), nullable=False, index=True
    )
    #: "state_changed" | "intervention_raised" | "intervention_resolved" |
    #: "verified" | "approved" | "approval_invalidated" | "submitted" | "failed"
    event_type: Mapped[str] = mapped_column(sa.String(60), nullable=False, index=True)
    from_state: Mapped[ApplicationState | None] = mapped_column(
        enum_column(ApplicationState, name="event_from_state"), nullable=True
    )
    to_state: Mapped[ApplicationState | None] = mapped_column(
        enum_column(ApplicationState, name="event_to_state"), nullable=True
    )
    intervention_reason: Mapped[InterventionReason | None] = mapped_column(
        enum_column(InterventionReason, name="event_intervention_reason"), nullable=True
    )
    #: Short, user-facing sentence. Never internal reasoning.
    message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    #: Redacted structured context.
    payload: Mapped[dict[str, object]] = mapped_column(nullable=False, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    #: Which user action caused this, when there was one.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    application: Mapped[Application] = relationship(back_populates="events")


class ApprovalRecordRow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A human's recorded consent to submit one specific form state.

    Named ``ApprovalRecordRow`` to keep it distinct from
    ``domain.approval.ApprovalRecord``, which is the pure dataclass the gate consumes.

    ``form_state_hash`` is what makes the approval specific. A new approval is a new
    row; approvals are never updated, so the history of what was approved and when
    stays intact.
    """

    __tablename__ = "approval_records"

    application_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    approved_by_user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    approved_at: Mapped[datetime] = mapped_column(server_default=sa.func.now(), nullable=False)
    form_state_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    #: The IP and user agent of the approving request, for the audit trail.
    request_context: Mapped[dict[str, object]] = mapped_column(nullable=False, default=dict)

    application: Mapped[Application] = relationship(back_populates="approvals")

    __table_args__ = (sa.Index("ix_approval_active", "application_id", "revoked_at"),)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
