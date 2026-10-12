"""In-app notifications.

Deduplication is the whole design. ``dedupe_key`` is unique per user, so telling
someone about the same job twice is impossible at the database level rather than
dependent on a check that a concurrent worker could race.

The key encodes the *reason*, not just the subject, so a genuinely new event about a
job the user already saw still gets through. When a posting materially changes, the
key includes a digest of what changed:

    job_match:<job_id>                        first time this job matched
    job_match:<job_id>:changed:<digest>       the posting changed materially

Email and Telegram are deliberately absent. Adding a channel column now would imply
delivery tracking that does not exist; those adapters arrive in Phase 4.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.models import Notification

logger = get_logger(__name__)


def job_match_dedupe_key(job_id: uuid.UUID, *, changed_fields: list[str] | None = None) -> str:
    """The dedupe key for a job-match notification."""
    if not changed_fields:
        return f"job_match:{job_id}"
    digest = hashlib.sha256("|".join(sorted(changed_fields)).encode()).hexdigest()[:12]
    return f"job_match:{job_id}:changed:{digest}"


async def create_notification(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    kind: str,
    title: str,
    dedupe_key: str,
    body: str | None = None,
    link: str | None = None,
    job_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    payload: dict[str, object] | None = None,
) -> Notification | None:
    """Create a notification, or return ``None`` if it is a duplicate.

    A duplicate is not an error: the caller is usually a background sweep that
    legitimately re-examines the same jobs, many times per call, across many users,
    in one long-lived session. Returning ``None`` lets it carry on.

    The conflicting insert runs inside its own ``SAVEPOINT`` (``begin_nested``) so
    that failing it only undoes this one row. A plain ``session.rollback()`` here
    would roll back the *entire* surrounding transaction — every object the caller
    loaded before this call (e.g. a sweep's already-loaded jobs and profiles)
    becomes expired, and the next attribute access on one of them raises
    ``MissingGreenlet`` trying to implicitly reload it. That only shows up once a
    real duplicate is actually hit partway through a longer unit of work, which a
    single-notification caller or a fresh-database test never exercises.
    """
    notification = Notification(
        user_id=user_id,
        kind=kind,
        title=title,
        body=body,
        link=link,
        job_id=job_id,
        application_id=application_id,
        dedupe_key=dedupe_key,
        payload=payload or {},
    )
    try:
        async with session.begin_nested():
            session.add(notification)
            await session.flush()
    except IntegrityError:
        logger.debug("notification_suppressed_as_duplicate", dedupe_key=dedupe_key)
        return None
    return notification


async def list_notifications(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Notification], int, int]:
    """A page of notifications, the total, and the unread count."""
    base = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        base = base.where(Notification.read_at.is_(None))

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    unread = (
        await session.execute(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        )
    ).scalar_one()

    rows = (
        (
            await session.execute(
                base.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total, unread


async def mark_read(
    session: AsyncSession, *, user_id: uuid.UUID, notification_id: uuid.UUID
) -> Notification:
    result = await session.execute(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user_id
        )
    )
    notification = result.scalar_one_or_none()
    if notification is None:
        raise NotFoundError("Notification not found.")
    if notification.read_at is None:
        notification.read_at = utcnow()
        await session.flush()
    return notification


async def mark_all_read(session: AsyncSession, *, user_id: uuid.UUID) -> int:
    rows = (
        (
            await session.execute(
                select(Notification).where(
                    Notification.user_id == user_id, Notification.read_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    now = utcnow()
    for notification in rows:
        notification.read_at = now
    await session.flush()
    return len(rows)


async def notify_job_matches(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    matches: list[tuple[uuid.UUID, str, str, float | None]],
    threshold: float,
) -> int:
    """Notify about jobs scoring at or above ``threshold``.

    ``matches`` is ``(job_id, job_title, company_name, score)``. A job with a ``None``
    score was not rankable and is skipped rather than notified about at an assumed
    value.
    """
    created = 0
    for job_id, title, company, score in matches:
        if score is None or score < threshold:
            continue
        notification = await create_notification(
            session,
            user_id=user_id,
            kind="job_match",
            title=f"{title} at {company}",
            # Phrased as a ranking, never as a probability of an outcome.
            body=f"Match score {score:.0%} against your profile. Review the breakdown.",
            link=f"/jobs/{job_id}",
            job_id=job_id,
            dedupe_key=job_match_dedupe_key(job_id),
            payload={"score": score, "score_kind": "RANKING_HEURISTIC"},
        )
        if notification is not None:
            created += 1
    return created
