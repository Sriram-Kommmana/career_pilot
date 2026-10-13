"""Applications: creation, duplicate prevention, preparation, and the tracker.

The submission gate and approval-invalidation behaviour get their own module,
``test_submission_endpoint.py``, because they need an application pushed past
``WAITING_FOR_USER_INPUT`` — a state no Phase 1 endpoint can reach on its own, since
that is where the Phase 5 browser agent will eventually do its work.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.domain.states import ApplicationState

pytestmark = pytest.mark.integration


async def _create_application(
    client: object, api: str, auth: dict[str, str], job_id: object
) -> dict[str, Any]:
    response = await client.post(  # type: ignore[attr-defined]
        f"{api}/applications", headers=auth, json={"job_id": str(job_id)}
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


class TestCreate:
    async def test_creating_an_application_starts_in_selected(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        body = await _create_application(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        assert body["state"] == ApplicationState.SELECTED.value
        assert body["job"]["id"] == str(seeded_job.id)  # type: ignore[attr-defined]

    async def test_a_second_application_for_the_same_job_is_refused(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        await _create_application(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications",
            headers=auth,
            json={"job_id": str(seeded_job.id)},  # type: ignore[attr-defined]
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "DUPLICATE_APPLICATION"

    async def test_two_different_users_may_each_apply_to_the_same_job(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        second_auth: dict[str, str],
        seeded_job: object,
    ) -> None:
        first = await _create_application(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        second = await _create_application(client, api, second_auth, seeded_job.id)  # type: ignore[attr-defined]
        assert first["id"] != second["id"]

    async def test_a_nonexistent_job_is_404(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications", headers=auth, json={"job_id": str(uuid.uuid4())}
        )
        assert response.status_code == 404

    async def test_unauthenticated_access_is_refused(
        self, client: object, api: str, seeded_job: object
    ) -> None:
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications",
            json={"job_id": str(seeded_job.id)},  # type: ignore[attr-defined]
        )
        assert response.status_code == 401


class TestOwnership:
    async def test_cannot_fetch_someone_elses_application(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        second_auth: dict[str, str],
        seeded_job: object,
    ) -> None:
        created = await _create_application(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        response = await client.get(  # type: ignore[attr-defined]
            f"{api}/applications/{created['id']}", headers=second_auth
        )
        assert response.status_code == 404

    async def test_cannot_cancel_someone_elses_application(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        second_auth: dict[str, str],
        seeded_job: object,
    ) -> None:
        created = await _create_application(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{created['id']}/cancel", headers=second_auth, json={}
        )
        assert response.status_code == 404


class TestPrepare:
    async def test_prepare_resolves_profile_backed_fields(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        await client.patch(  # type: ignore[attr-defined]
            f"{api}/profile", headers=auth, json={"full_name": "Demo Candidate"}
        )
        created = await _create_application(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{created['id']}/prepare", headers=auth
        )
        assert response.status_code == 200
        body = response.json()
        fields = {f["field_key"]: f for f in body["fields"]}
        assert fields["full_name"]["resolution"] == "RESOLVED"
        assert fields["full_name"]["value_masked"] == "Demo Candidate"
        assert fields["full_name"]["value_provenance"] == "USER_CONFIRMED_PROFILE"

    async def test_sensitive_questions_need_a_human_and_are_never_guessed(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        created = await _create_application(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{created['id']}/prepare", headers=auth
        )
        body = response.json()
        fields = {f["field_key"]: f for f in body["fields"]}

        for key in ("work_authorisation", "requires_sponsorship"):
            assert fields[key]["resolution"] == "NEEDS_HUMAN"
            assert fields[key]["value_masked"] is None
            assert fields[key]["value_provenance"] == "UNKNOWN"

        assert body["state"] == ApplicationState.WAITING_FOR_USER_INPUT.value
        assert "NEEDS_HUMAN" in body["unresolved_interventions"]

    async def test_a_masked_field_never_exposes_the_real_value(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        await client.patch(  # type: ignore[attr-defined]
            f"{api}/profile", headers=auth, json={"phone": "9876543210"}
        )
        created = await _create_application(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{created['id']}/prepare", headers=auth
        )
        fields = {f["field_key"]: f for f in response.json()["fields"]}
        assert "9876543210" not in response.text
        assert fields["phone"]["value_masked"].endswith("3210")

    async def test_preparing_a_closed_job_raises_the_intervention(
        self, client: object, session: object, api: str, auth: dict[str, str]
    ) -> None:
        from datetime import UTC, datetime

        from app.domain.normalize import (
            NormalizedJob,
            PostedAtPrecision,
            SourceStatus,
        )
        from app.services.jobs import upsert_job

        job, _ = await upsert_job(
            session,  # type: ignore[arg-type]
            normalized=NormalizedJob(
                source="demo_seed",
                source_job_id="closed-prepare",
                company_name="Testcorp",
                title="Closed Role",
                application_url="https://example.invalid/closed",
                posted_at=datetime(2026, 6, 1, tzinfo=UTC),
                posted_at_precision=PostedAtPrecision.DATE_ONLY,
                source_status=SourceStatus.CLOSED,
            ),
            source_external_key="test-suite",
            is_demo_data=True,
        )
        await session.commit()  # type: ignore[attr-defined]

        created = await _create_application(client, api, auth, job.id)
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{created['id']}/prepare", headers=auth
        )
        assert response.status_code == 200
        assert "JOB_CLOSED" in response.json()["unresolved_interventions"]

    async def test_answering_a_field_resolves_it_and_can_clear_the_intervention(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        await client.patch(  # type: ignore[attr-defined]
            f"{api}/profile", headers=auth, json={"full_name": "Demo Candidate"}
        )
        created = await _create_application(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{created['id']}/prepare", headers=auth
        )

        for field_key, value in (
            ("work_authorisation", "Yes, I am authorised"),
            ("requires_sponsorship", "No"),
            ("resume", "my-resume.pdf"),
        ):
            response = await client.post(  # type: ignore[attr-defined]
                f"{api}/applications/{created['id']}/answer",
                headers=auth,
                json={"field_key": field_key, "value": value},
            )
            assert response.status_code == 200, response.text

        final = response.json()
        assert final["state"] == ApplicationState.READY.value
        assert final["unresolved_interventions"] == []

    async def test_answering_an_empty_value_is_rejected(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        created = await _create_application(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{created['id']}/prepare", headers=auth
        )
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{created['id']}/answer",
            headers=auth,
            json={"field_key": "work_authorisation", "value": "   "},
        )
        assert response.status_code == 422

    async def test_bare_fill_route_no_longer_exists(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        """Phase 5 replaced the bare ``/fill`` 501 stub with the real
        browser-session workflow (``/browser-session``, ``/browser-session/inspect``,
        ``/browser-session/fill``) — see ``tests/integration/test_browser_session.py``
        and ``tests/integration/test_browser_fill_verification.py`` for that real
        behaviour. This only pins down that the old placeholder path is gone, not
        silently resurrected."""
        created = await _create_application(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{created['id']}/fill", headers=auth
        )
        assert response.status_code == 404


class TestCancelAndOutcome:
    async def test_cancel_moves_to_cancelled_and_allows_a_fresh_application(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        created = await _create_application(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        cancelled = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{created['id']}/cancel",
            headers=auth,
            json={"reason": "Changed my mind"},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == ApplicationState.CANCELLED.value

        # Cancelled applications are excluded from the duplicate check.
        again = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications",
            headers=auth,
            json={"job_id": str(seeded_job.id)},  # type: ignore[attr-defined]
        )
        assert again.status_code == 201

    async def test_outcome_is_only_ever_set_by_the_user(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        created = await _create_application(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        response = await client.patch(  # type: ignore[attr-defined]
            f"{api}/applications/{created['id']}/outcome",
            headers=auth,
            json={"outcome": "INTERVIEW", "note": "Phone screen scheduled"},
        )
        assert response.status_code == 200
        assert response.json()["outcome"] == "INTERVIEW"
        assert response.json()["outcome_note"] == "Phone screen scheduled"


class TestTracker:
    async def test_tracker_groups_applications_by_column(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        created = await _create_application(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        response = await client.get(f"{api}/applications/tracker", headers=auth)  # type: ignore[attr-defined]
        assert response.status_code == 200
        body = response.json()
        assert "saved" in body["column_order"]
        saved_ids = {item["id"] for item in body["columns"]["saved"]}
        assert created["id"] in saved_ids

    async def test_tracker_only_shows_the_caller_s_own_applications(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        second_auth: dict[str, str],
        seeded_job: object,
    ) -> None:
        await _create_application(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        response = await client.get(  # type: ignore[attr-defined]
            f"{api}/applications/tracker", headers=second_auth
        )
        all_ids = [item["id"] for column in response.json()["columns"].values() for item in column]
        assert all_ids == []
