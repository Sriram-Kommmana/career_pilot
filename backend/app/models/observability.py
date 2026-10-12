"""Notifications, agent runs, and tool executions.

These tables answer "what did the agent actually do, and why am I seeing this job?".

What they deliberately do **not** hold is the model's internal reasoning. ``summary``
is a short, user-facing sentence; ``tool_executions`` records the calls made and their
redacted results. Chain-of-thought is never persisted or exposed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Notification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An in-app notification.

    ``dedupe_key`` plus a unique constraint is how a user is not told about the same
    job twice. It encodes the reason as well as the subject — ``"job_match:<job_id>"``
    — so a genuinely new event about the same job (the posting materially changed,
    say) produces a different key and is allowed through.

    Email and Telegram stay out of this table on purpose: a channel row would imply
    delivery tracking that does not exist yet. Those adapters arrive in Phase 4.
    """

    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: "job_match" | "application_needs_input" | "application_needs_approval" |
    #: "application_submitted" | "connector_failed"
    kind: Mapped[str] = mapped_column(sa.String(60), nullable=False, index=True)
    title: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    #: Where clicking it goes, e.g. "/jobs/<id>".
    link: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)

    job_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=True, index=True
    )

    dedupe_key: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(nullable=False, default=dict)

    __table_args__ = (
        sa.UniqueConstraint("user_id", "dedupe_key", name="user_id_dedupe_key"),
        sa.Index("ix_notifications_user_unread", "user_id", "read_at"),
    )

    @property
    def is_read(self) -> bool:
        return self.read_at is not None


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One execution of an agent graph or a background job.

    ``correlation_id`` is the same value that appears in every log line and in the
    error envelope of a failed request, so a user-reported problem can be traced to
    the exact run without guessing.
    """

    __tablename__ = "agent_runs"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("applications.id", ondelete="SET NULL"), nullable=True, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )

    #: "discover" | "analyze" | "prepare" | "fill" | "verify" | "submit"
    kind: Mapped[str] = mapped_column(sa.String(40), nullable=False, index=True)
    #: "running" | "succeeded" | "failed" | "waiting_for_user" | "cancelled"
    status: Mapped[str] = mapped_column(
        sa.String(30), nullable=False, default="running", index=True
    )
    correlation_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)

    started_at: Mapped[datetime] = mapped_column(server_default=sa.func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    #: A short, user-facing account of what happened. Not chain-of-thought.
    summary: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    #: Redacted structured context: counts, sources touched, states entered.
    context: Mapped[dict[str, object]] = mapped_column(nullable=False, default=dict)

    tool_executions: Mapped[list[ToolExecution]] = relationship(
        back_populates="agent_run",
        cascade="all, delete-orphan",
        order_by="ToolExecution.started_at",
    )

    __table_args__ = (
        sa.CheckConstraint("retry_count >= 0", name="retry_count_non_negative"),
        sa.Index("ix_agent_runs_user_started", "user_id", sa.text("started_at DESC")),
    )


class ToolExecution(UUIDPrimaryKeyMixin, Base):
    """One tool call inside an agent run.

    Arguments and results are stored **redacted** — passed through
    ``domain.redaction`` before they reach this table — because this is exactly the
    place a form value or a page excerpt would otherwise leak into permanent storage.
    """

    __tablename__ = "tool_executions"

    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    tool_name: Mapped[str] = mapped_column(sa.String(120), nullable=False, index=True)
    #: "succeeded" | "failed" | "skipped"
    status: Mapped[str] = mapped_column(sa.String(30), nullable=False, default="succeeded")

    started_at: Mapped[datetime] = mapped_column(server_default=sa.func.now(), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    arguments_redacted: Mapped[dict[str, object]] = mapped_column(nullable=False, default=dict)
    result_redacted: Mapped[dict[str, object]] = mapped_column(nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    #: Path to a screenshot or trace on disk. The artefact is scrubbed before writing.
    artifact_path: Mapped[str | None] = mapped_column(sa.String(1000), nullable=True)

    agent_run: Mapped[AgentRun] = relationship(back_populates="tool_executions")

    __table_args__ = (
        sa.UniqueConstraint("agent_run_id", "sequence", name="agent_run_id_sequence"),
    )
