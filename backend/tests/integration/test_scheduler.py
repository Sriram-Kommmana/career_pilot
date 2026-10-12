"""``app.services.scheduler`` (the notification sweep) and the ``sweep`` CLI command.

Phase 4A's whole point: discover postings and notify users on threshold, reusing
``discovery``/``matching``/``notifications`` as-is. Nothing here creates or touches
an ``Application`` row — that is Phase 4B.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.domain.normalize import NormalizedJob, PostedAtPrecision, SourceStatus, WorkArrangement
from app.models import AgentRun, Job, JobMatchAssessment
from app.services import matching as matching_service
from app.services import notifications as notifications_service
from app.services import scheduler as scheduler_service
from app.services.jobs import get_or_create_source, upsert_job
from app.services.profile import add_child, get_profile, update_profile
from tests.integration.test_discovery import _install_mock_connector

pytestmark = pytest.mark.integration


async def _user_id(client: object, headers: dict[str, str], api: str) -> uuid.UUID:
    response = await client.get(f"{api}/auth/me", headers=headers)  # type: ignore[attr-defined]
    assert response.status_code == 200
    return uuid.UUID(response.json()["id"])


async def _real_job(
    session: object,
    *,
    source_job_id: str,
    requirements: tuple[str, ...] = ("Experience with Python",),
) -> Job:
    normalized = NormalizedJob(
        source="demo_seed",
        source_job_id=source_job_id,
        company_name="Acme Corp",
        title="Backend Engineer",
        location_raw="Remote",
        application_url=f"https://example.invalid/apply/{source_job_id}",
        requirements=requirements,
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


async def _give_matching_skill(session: object, *, user_id: uuid.UUID) -> None:
    """A confirmed skill that clears the ``_real_job`` default requirement, so the
    sweep produces a concrete (non-None) score rather than an unassessed one."""
    await add_child(
        session, user_id=user_id, collection="skills", payload={"name": "Python"}  # type: ignore[arg-type]
    )


async def _set_threshold(session: object, *, user_id: uuid.UUID, threshold: float) -> None:
    await update_profile(
        session, user_id=user_id, changes={"notify_score_threshold": threshold}  # type: ignore[arg-type]
    )


class TestNotificationSweep:
    async def test_a_user_above_threshold_is_notified(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        user_id = await _user_id(client, auth, api)
        await _give_matching_skill(session, user_id=user_id)
        await _set_threshold(session, user_id=user_id, threshold=0.1)
        await _real_job(session, source_job_id="sweep-above-threshold")

        result = await scheduler_service.run_notification_sweep(
            session, settings=get_settings()  # type: ignore[arg-type]
        )
        assert result.notifications_created == 1
        assert result.users_notified == 1
        assert result.users_failed == 0

    async def test_a_user_below_threshold_is_not_notified(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        user_id = await _user_id(client, auth, api)
        await _give_matching_skill(session, user_id=user_id)
        await _set_threshold(session, user_id=user_id, threshold=0.99)
        await _real_job(session, source_job_id="sweep-below-threshold")

        result = await scheduler_service.run_notification_sweep(
            session, settings=get_settings()  # type: ignore[arg-type]
        )
        assert result.notifications_created == 0

    async def test_running_the_sweep_twice_does_not_duplicate_the_notification(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        user_id = await _user_id(client, auth, api)
        await _give_matching_skill(session, user_id=user_id)
        await _set_threshold(session, user_id=user_id, threshold=0.1)
        await _real_job(session, source_job_id="sweep-dedup")

        first = await scheduler_service.run_notification_sweep(
            session, settings=get_settings()  # type: ignore[arg-type]
        )
        second = await scheduler_service.run_notification_sweep(
            session, settings=get_settings()  # type: ignore[arg-type]
        )
        assert first.notifications_created == 1
        assert second.notifications_created == 0, (
            "the database-backed dedupe key must suppress the second, identical sweep"
        )

    async def test_demo_jobs_are_never_scored_or_notified_about(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        user_id = await _user_id(client, auth, api)
        await _give_matching_skill(session, user_id=user_id)
        await _set_threshold(session, user_id=user_id, threshold=0.0)

        normalized = NormalizedJob(
            source="demo_seed",
            source_job_id="sweep-demo-job",
            company_name="Acme Corp",
            title="Backend Engineer",
            location_raw="Remote",
            application_url="https://example.invalid/apply/sweep-demo-job",
            requirements=("Experience with Python",),
            work_arrangement=WorkArrangement.REMOTE,
            posted_at_precision=PostedAtPrecision.UNKNOWN,
            source_status=SourceStatus.OPEN,
        )
        await upsert_job(
            session,  # type: ignore[arg-type]
            normalized=normalized,
            source_external_key="test-suite",
            is_demo_data=True,
        )
        await session.flush()  # type: ignore[attr-defined]

        result = await scheduler_service.run_notification_sweep(
            session, settings=get_settings()  # type: ignore[arg-type]
        )
        assert result.jobs_scored == 0
        assert result.notifications_created == 0

    async def test_one_users_failure_does_not_block_another_users_notification(
        self,
        session: object,
        client: object,
        api: str,
        auth: dict[str, str],
        second_auth: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        broken_user_id = await _user_id(client, auth, api)
        healthy_user_id = await _user_id(client, second_auth, api)
        for user_id in (broken_user_id, healthy_user_id):
            await _give_matching_skill(session, user_id=user_id)
            await _set_threshold(session, user_id=user_id, threshold=0.1)
        await _real_job(session, source_job_id="sweep-partial-failure")

        async def _flaky_get_profile(session_arg: object, *, user_id: uuid.UUID) -> object:
            if user_id == broken_user_id:
                raise RuntimeError("simulated profile failure")
            return await get_profile(session_arg, user_id=user_id)  # type: ignore[arg-type]

        monkeypatch.setattr(scheduler_service, "get_profile", _flaky_get_profile)

        result = await scheduler_service.run_notification_sweep(
            session, settings=get_settings()  # type: ignore[arg-type]
        )
        assert result.users_failed == 1
        assert result.notifications_created == 1
        assert result.users_notified == 1

    async def test_a_failure_partway_through_one_users_scoring_leaves_no_partial_row(
        self,
        session: object,
        client: object,
        api: str,
        auth: dict[str, str],
        second_auth: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression test for a real defect found during the Phase 4A audit.

        ``analyze_many`` stages a ``JobMatchAssessment`` via ``session.add()`` per
        job as it loops, flushing only once at the end. If it raises partway
        through — succeeding on one job, failing on the next — the already-added
        row for the first job was never rolled back: it stayed pending in the
        session and was later flushed anyway by the *next* user's notification
        insert, which flushes the whole unit of work, not just its own row. That
        silently persisted a partial, never-reviewed assessment for a user the
        sweep had just reported as failed.

        Fixed by scoping each user's work to its own SAVEPOINT in
        ``run_notification_sweep``, so a failure rolls back exactly that user's
        staged work and nothing else.
        """
        broken_user_id = await _user_id(client, auth, api)
        healthy_user_id = await _user_id(client, second_auth, api)
        for user_id in (broken_user_id, healthy_user_id):
            await _give_matching_skill(session, user_id=user_id)
            await _set_threshold(session, user_id=user_id, threshold=0.1)
        # A marker only the broken user's CandidateProfile carries, so the failure
        # is tied to *which user* is being scored rather than to call order —
        # `run_notification_sweep` makes no ordering guarantee over its user list.
        await update_profile(
            session, user_id=broken_user_id, changes={"target_roles": ["audit-marker"]}  # type: ignore[arg-type]
        )
        await _real_job(session, source_job_id="partial-write-job-1")
        await _real_job(session, source_job_id="partial-write-job-2")

        from app.domain.matching import assess_match as real_assess_match

        calls_for_marked_profile = {"n": 0}

        def _flaky_assess_match(*, profile: object, job: object, config: object) -> object:
            if "audit-marker" in profile.target_roles:  # type: ignore[attr-defined]
                calls_for_marked_profile["n"] += 1
                if calls_for_marked_profile["n"] == 2:
                    raise RuntimeError("simulated mid-loop scoring failure")
            return real_assess_match(profile=profile, job=job, config=config)  # type: ignore[arg-type]

        monkeypatch.setattr(matching_service, "assess_match", _flaky_assess_match)

        result = await scheduler_service.run_notification_sweep(
            session, settings=get_settings()  # type: ignore[arg-type]
        )

        broken_rows = (
            await session.execute(  # type: ignore[attr-defined]
                select(JobMatchAssessment).where(JobMatchAssessment.user_id == broken_user_id)
            )
        ).scalars().all()
        assert broken_rows == [], (
            "the first job's assessment must not survive the second job's failure"
        )

        # Two jobs, both matching the healthy user's skill, both above threshold —
        # one notification per job.
        _, healthy_total, _ = await notifications_service.list_notifications(
            session, user_id=healthy_user_id  # type: ignore[arg-type]
        )
        assert healthy_total == 2, (
            "a healthy user processed in the same sweep must still be notified"
        )

        assert result.users_failed == 1
        assert result.users_notified == 1

        agent_run = (
            await session.execute(  # type: ignore[attr-defined]
                select(AgentRun).where(AgentRun.id == result.agent_run_id)
            )
        ).scalar_one()
        assert agent_run.status == "succeeded"

    async def test_no_users_at_all_is_safe(self, session: object) -> None:
        result = await scheduler_service.run_notification_sweep(
            session, settings=get_settings()  # type: ignore[arg-type]
        )
        assert result.users_considered == 0
        assert result.notifications_created == 0

    async def test_no_open_jobs_at_all_is_safe(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        user_id = await _user_id(client, auth, api)
        await _give_matching_skill(session, user_id=user_id)

        result = await scheduler_service.run_notification_sweep(
            session, settings=get_settings()  # type: ignore[arg-type]
        )
        assert result.users_considered == 1
        assert result.jobs_scored == 0
        assert result.notifications_created == 0

    async def test_the_agent_run_audit_row_has_no_owning_user(
        self, session: object
    ) -> None:
        result = await scheduler_service.run_notification_sweep(
            session, settings=get_settings()  # type: ignore[arg-type]
        )
        row = (
            await session.execute(  # type: ignore[attr-defined]
                select(AgentRun).where(AgentRun.id == result.agent_run_id)
            )
        ).scalar_one()
        assert row.user_id is None
        assert row.status == "succeeded"


class TestSweepLock:
    async def test_a_held_lock_blocks_a_second_connection_until_released(
        self, db_engine: object
    ) -> None:
        holder = await db_engine.connect()  # type: ignore[attr-defined]
        contender = await db_engine.connect()  # type: ignore[attr-defined]
        try:
            assert await scheduler_service.try_acquire_sweep_lock(holder) is True
            assert await scheduler_service.try_acquire_sweep_lock(contender) is False

            await scheduler_service.release_sweep_lock(holder)

            assert await scheduler_service.try_acquire_sweep_lock(contender) is True
        finally:
            await scheduler_service.release_sweep_lock(contender)
            await holder.close()
            await contender.close()


class TestSweepCliExitStatus:
    """Exercises ``app.cli._run_sweep`` directly (the function ``sweep`` dispatches
    to), not a subprocess — the argparse wiring itself is trivial, matching how the
    rest of this suite treats ``cli.py``.

    ``_run_sweep`` calls ``resolve_database_url`` for the same fail-fast reason
    ``cmd_serve`` does. That function resolves the *real*, non-test settings' URL,
    which would try to reach (or start) the embedded development database rather
    than the test one. It is neutralised here; everything else in ``_run_sweep``
    already runs against the test database because ``db_engine`` points the
    process-global engine at it before this test ever calls into the CLI.
    """

    @pytest.fixture(autouse=True)
    def _no_real_database_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.core.config as config_module

        monkeypatch.setattr(config_module, "resolve_database_url", lambda settings: "unused")

    async def test_a_normal_run_with_nothing_configured_exits_zero(self) -> None:
        from app.cli import _run_sweep

        assert await _run_sweep() == 0

    async def test_a_failing_discovery_source_still_exits_zero(
        self, session: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.cli import _run_sweep

        _install_mock_connector(monkeypatch, {"badboard": "timeout"})
        await get_or_create_source(session, kind="greenhouse", external_key="badboard")  # type: ignore[arg-type]
        await session.commit()  # type: ignore[attr-defined]

        assert await _run_sweep() == 0

    async def test_a_lock_held_by_another_connection_is_skipped_safely(
        self, db_engine: object, session: object, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from app.cli import _run_sweep

        holder = await db_engine.connect()  # type: ignore[attr-defined]
        try:
            assert await scheduler_service.try_acquire_sweep_lock(holder) is True

            exit_code = await _run_sweep()
            assert exit_code == 0

            output = capsys.readouterr().out
            assert "already running" in output.lower()

            rows = (
                await session.execute(  # type: ignore[attr-defined]
                    select(AgentRun).where(AgentRun.kind == "notify_sweep")
                )
            ).scalars().all()
            assert rows == [], "a skipped sweep must not create a notify_sweep audit row"
        finally:
            await scheduler_service.release_sweep_lock(holder)
            await holder.close()
