"""``app.services.notifications`` — deduplication, threshold filtering, ownership.

Had zero test coverage before this file: the service was fully implemented but
never called from anywhere in the app, so none of this was exercised. Phase 4A's
scheduled sweep is the first real caller; these tests cover the service on its own,
independent of the sweep that now drives it (see ``test_scheduler.py``).
"""

from __future__ import annotations

import uuid

import pytest

from app.domain.normalize import NormalizedJob, PostedAtPrecision, SourceStatus, WorkArrangement
from app.models import Job
from app.services import notifications as notifications_service
from app.services.jobs import upsert_job

pytestmark = pytest.mark.integration


async def _user_id(client: object, headers: dict[str, str], api: str) -> uuid.UUID:
    response = await client.get(f"{api}/auth/me", headers=headers)  # type: ignore[attr-defined]
    assert response.status_code == 200
    return uuid.UUID(response.json()["id"])


async def _real_job(session: object, *, source_job_id: str) -> Job:
    """A persisted job row — ``Notification.job_id`` is a real foreign key, so a
    notification about a job that doesn't exist is rejected at the database level,
    not just logically wrong."""
    normalized = NormalizedJob(
        source="demo_seed",
        source_job_id=source_job_id,
        company_name="Acme Corp",
        title="Backend Engineer",
        location_raw="Remote",
        application_url=f"https://example.invalid/apply/{source_job_id}",
        requirements=("Experience with Python",),
        work_arrangement=WorkArrangement.REMOTE,
        posted_at_precision=PostedAtPrecision.UNKNOWN,
        source_status=SourceStatus.OPEN,
    )
    job, _ = await upsert_job(
        session,  # type: ignore[arg-type]
        normalized=normalized,
        source_external_key="test-suite",
        is_demo_data=False,
    )
    await session.flush()  # type: ignore[attr-defined]
    return job


class TestCreateNotificationDedup:
    async def test_a_duplicate_dedupe_key_for_the_same_user_is_suppressed(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        user_id = await _user_id(client, auth, api)

        first = await notifications_service.create_notification(
            session,  # type: ignore[arg-type]
            user_id=user_id,
            kind="job_match",
            title="Backend Engineer at Acme",
            dedupe_key="job_match:same-key",
        )
        assert first is not None

        second = await notifications_service.create_notification(
            session,  # type: ignore[arg-type]
            user_id=user_id,
            kind="job_match",
            title="Backend Engineer at Acme",
            dedupe_key="job_match:same-key",
        )
        assert second is None, "a duplicate dedupe_key for the same user must be suppressed"

    async def test_the_same_dedupe_key_for_a_different_user_is_not_a_duplicate(
        self,
        session: object,
        client: object,
        api: str,
        auth: dict[str, str],
        second_auth: dict[str, str],
    ) -> None:
        user_a = await _user_id(client, auth, api)
        user_b = await _user_id(client, second_auth, api)

        first = await notifications_service.create_notification(
            session,  # type: ignore[arg-type]
            user_id=user_a,
            kind="job_match",
            title="Backend Engineer at Acme",
            dedupe_key="job_match:shared-key",
        )
        second = await notifications_service.create_notification(
            session,  # type: ignore[arg-type]
            user_id=user_b,
            kind="job_match",
            title="Backend Engineer at Acme",
            dedupe_key="job_match:shared-key",
        )
        assert first is not None
        assert second is not None, "dedup is per-user, not global"


class TestNotifyJobMatches:
    async def test_a_null_score_is_never_notified(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        user_id = await _user_id(client, auth, api)
        job = await _real_job(session, source_job_id="notify-null-score")
        created = await notifications_service.notify_job_matches(
            session,  # type: ignore[arg-type]
            user_id=user_id,
            matches=[(job.id, "Backend Engineer", "Acme", None)],
            threshold=0.0,
        )
        assert created == 0

    async def test_a_sub_threshold_score_is_not_notified(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        user_id = await _user_id(client, auth, api)
        job = await _real_job(session, source_job_id="notify-sub-threshold")
        created = await notifications_service.notify_job_matches(
            session,  # type: ignore[arg-type]
            user_id=user_id,
            matches=[(job.id, "Backend Engineer", "Acme", 0.4)],
            threshold=0.6,
        )
        assert created == 0

    async def test_a_score_at_or_above_threshold_is_notified_exactly_once(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        user_id = await _user_id(client, auth, api)
        job = await _real_job(session, source_job_id="notify-at-threshold")
        created = await notifications_service.notify_job_matches(
            session,  # type: ignore[arg-type]
            user_id=user_id,
            matches=[(job.id, "Backend Engineer", "Acme", 0.6)],
            threshold=0.6,
        )
        assert created == 1

        rows, total, unread = await notifications_service.list_notifications(
            session, user_id=user_id  # type: ignore[arg-type]
        )
        assert total == 1
        assert unread == 1
        assert rows[0].job_id == job.id


class TestOwnership:
    async def test_a_user_cannot_mark_another_users_notification_read(
        self,
        session: object,
        client: object,
        api: str,
        auth: dict[str, str],
        second_auth: dict[str, str],
    ) -> None:
        owner_id = await _user_id(client, auth, api)
        notification = await notifications_service.create_notification(
            session,  # type: ignore[arg-type]
            user_id=owner_id,
            kind="job_match",
            title="Backend Engineer at Acme",
            dedupe_key="job_match:ownership-test",
        )
        assert notification is not None
        await session.commit()  # type: ignore[attr-defined]

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/notifications/{notification.id}/read", headers=second_auth
        )
        assert response.status_code == 404
