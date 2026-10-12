"""Notifications, agent runs, and the activity stream."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from app.api.deps import CurrentUserId, DbSession
from app.core.errors import NotFoundError
from app.models import AgentRun
from app.schemas.common import MessageResponse, Page
from app.schemas.observability import (
    AgentRunDetailOut,
    AgentRunOut,
    NotificationListOut,
    NotificationOut,
)
from app.services import events as events_service
from app.services import notifications as notifications_service

router = APIRouter(tags=["observability"])


@router.get("/notifications", response_model=NotificationListOut)
async def list_notifications(
    session: DbSession,
    user_id: CurrentUserId,
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NotificationListOut:
    rows, total, unread = await notifications_service.list_notifications(
        session, user_id=user_id, unread_only=unread_only, limit=limit, offset=offset
    )
    return NotificationListOut(
        items=[NotificationOut.model_validate(row) for row in rows],
        total=total,
        unread=unread,
        limit=limit,
        offset=offset,
    )


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
async def mark_notification_read(
    notification_id: Annotated[uuid.UUID, Path()],
    session: DbSession,
    user_id: CurrentUserId,
) -> NotificationOut:
    notification = await notifications_service.mark_read(
        session, user_id=user_id, notification_id=notification_id
    )
    return NotificationOut.model_validate(notification)


@router.post("/notifications/read-all", response_model=MessageResponse)
async def mark_all_notifications_read(
    session: DbSession, user_id: CurrentUserId
) -> MessageResponse:
    count = await notifications_service.mark_all_read(session, user_id=user_id)
    return MessageResponse(message=f"Marked {count} notification(s) as read.")


@router.get("/agent-runs", response_model=Page[AgentRunOut])
async def list_agent_runs(
    session: DbSession,
    user_id: CurrentUserId,
    kind: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AgentRunOut]:
    base = select(AgentRun).where(AgentRun.user_id == user_id)
    if kind:
        base = base.where(AgentRun.kind == kind)

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                base.order_by(AgentRun.started_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return Page[AgentRunOut](
        items=[AgentRunOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/agent-runs/{run_id}", response_model=AgentRunDetailOut)
async def get_agent_run(
    run_id: Annotated[uuid.UUID, Path()], session: DbSession, user_id: CurrentUserId
) -> AgentRunDetailOut:
    """One run with its tool executions.

    Tool arguments and results were redacted when written, so nothing sensitive is
    exposed here. Chain-of-thought is never stored and never returned.
    """
    row = (
        await session.execute(
            select(AgentRun)
            .where(AgentRun.id == run_id, AgentRun.user_id == user_id)
            .options(selectinload(AgentRun.tool_executions))
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Agent run not found.")
    return AgentRunDetailOut.model_validate(row)


@router.get("/events")
async def activity_stream(user_id: CurrentUserId) -> EventSourceResponse:
    """Live agent activity for the signed-in user, as Server-Sent Events.

    Each frame is a short, user-facing description of what the agent is doing. Frames
    never carry chain-of-thought, prompts, or raw page content.

    Phase 1 publishes through an in-process broker, so a single API process sees every
    event. Phase 4 swaps the broker for Redis pub/sub without changing this contract.
    """
    return EventSourceResponse(events_service.stream(user_id))
