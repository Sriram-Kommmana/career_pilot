"""Notification and agent-run contracts.

``AgentRunOut.summary`` is a short, user-facing account of what happened. Neither this
model nor :class:`ToolExecutionOut` exposes chain-of-thought, prompts, or raw page
content; tool arguments and results are stored already redacted.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.schemas.common import ApiModel


class NotificationOut(ApiModel):
    id: uuid.UUID
    kind: str
    title: str
    body: str | None
    link: str | None
    job_id: uuid.UUID | None
    application_id: uuid.UUID | None
    read_at: datetime | None
    created_at: datetime
    payload: dict[str, object] = Field(default_factory=dict)


class NotificationListOut(ApiModel):
    items: list[NotificationOut]
    total: int
    unread: int
    limit: int
    offset: int


class ToolExecutionOut(ApiModel):
    id: uuid.UUID
    sequence: int
    tool_name: str
    status: str
    started_at: datetime
    duration_ms: int | None
    #: Already redacted at write time; see app.domain.redaction.
    arguments_redacted: dict[str, object] = Field(default_factory=dict)
    result_redacted: dict[str, object] = Field(default_factory=dict)
    error: str | None = None
    artifact_path: str | None = None


class AgentRunOut(ApiModel):
    id: uuid.UUID
    kind: str
    status: str
    correlation_id: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    #: A concise decision summary, not internal reasoning.
    summary: str | None
    failure_reason: str | None
    retry_count: int
    job_id: uuid.UUID | None
    application_id: uuid.UUID | None
    context: dict[str, object] = Field(default_factory=dict)


class AgentRunDetailOut(AgentRunOut):
    tool_executions: list[ToolExecutionOut] = Field(default_factory=list)
