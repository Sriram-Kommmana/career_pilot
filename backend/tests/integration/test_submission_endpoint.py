"""The submission gate, exercised through the real HTTP endpoints and a real database.

``tests/unit/test_submission_gate.py`` proves the gate's logic in isolation. This
module proves the three things that logic alone cannot: that the API wires the gate
in correctly, that a request which fails after partially writing state leaves nothing
behind (the transaction rolls back), and that answering a field after approval really
does invalidate it end to end.

Phase 1 ships no browser agent, so no HTTP endpoint can push an application past
``WAITING_FOR_USER_INPUT`` on its own — that is Phase 5's job. These tests stand in
for the browser agent at the service layer (via ``advance_to_waiting_for_approval``)
and then hand off to the HTTP client for the part that actually matters: the gate.
"""

from __future__ import annotations

import uuid

import pytest

from app.domain.states import ApplicationState
from tests.conftest import advance_to_waiting_for_approval

pytestmark = pytest.mark.integration


async def _user_id(client: object, api: str, headers: dict[str, str]) -> uuid.UUID:
    response = await client.get(f"{api}/auth/me", headers=headers)  # type: ignore[attr-defined]
    return uuid.UUID(response.json()["id"])


async def _create_and_prepare(
    client: object, api: str, auth: dict[str, str], job_id: object
) -> dict[str, object]:
    created = await client.post(  # type: ignore[attr-defined]
        f"{api}/applications", headers=auth, json={"job_id": str(job_id)}
    )
    assert created.status_code == 201, created.text
    prepared = await client.post(  # type: ignore[attr-defined]
        f"{api}/applications/{created.json()['id']}/prepare", headers=auth
    )
    assert prepared.status_code == 200, prepared.text
    return prepared.json()  # type: ignore[no-any-return]


class TestApproveBeforeVerification:
    async def test_approve_is_refused_before_the_form_is_filled_and_verified(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        """Safety requirement 3's precondition: there is nothing to approve yet."""
        application = await _create_and_prepare(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/approve",
            headers=auth,
            json={"form_state_hash": application["form_state_hash"]},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_STATE_TRANSITION"


class TestSubmitRequestShape:
    async def test_submit_without_explicit_confirmation_is_rejected(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        application = await _create_and_prepare(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/submit",
            headers=auth,
            json={"confirm": False, "form_state_hash": "a" * 64},
        )
        assert response.status_code == 422

    async def test_submit_with_a_stale_hash_is_refused_before_the_gate_even_runs(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        application = await _create_and_prepare(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": "0" * 64},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "APPROVAL_STALE"

    async def test_submit_with_no_approval_and_no_browser_session_is_refused(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        """Phase 6: a real submission requires a real, open, inspected browser
        session before the gate is even consulted (see the explicit check order
        in ``app.services.browser_submit.submit``) — so with none open, this
        refuses on that alone, before ``NO_APPROVAL``/``NO_VERIFICATION``/
        ``WRONG_STATE`` would otherwise apply. The gate's own blocker-listing
        behaviour is unchanged and still covered directly by
        ``tests/unit/test_submission_gate.py``.
        """
        application = await _create_and_prepare(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": application["form_state_hash"]},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "BROWSER_SESSION_NOT_FOUND"


class TestFullGatePass:
    async def test_review_shows_only_the_approval_blocker_once_verified(
        self, client: object, session: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        application = await _create_and_prepare(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        user_id = await _user_id(client, api, auth)

        new_hash = await advance_to_waiting_for_approval(
            session,
            user_id=user_id,
            application_id=uuid.UUID(application["id"]),  # type: ignore[arg-type]
        )
        await session.commit()  # type: ignore[attr-defined]

        review = await client.get(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/review", headers=auth
        )
        assert review.status_code == 200
        decision = review.json()["decision"]
        assert decision["allowed"] is False
        assert [b["code"] for b in decision["blockers"]] == ["NO_APPROVAL"]
        assert review.json()["application"]["form_state_hash"] == new_hash

    async def test_approve_then_submit_without_a_browser_session_rolls_back_cleanly(
        self, client: object, session: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        """The load-bearing test of this module.

        Every *database-side* gate condition is satisfied — approved, verified,
        the right state. The real submission click (Phase 6, exercised with a
        real browser in ``tests/integration/test_browser_submission.py``) still
        requires an actual open, inspected browser session, which this test
        deliberately does not create (``advance_to_waiting_for_approval`` is a
        DB-only test stand-in, not a real browser fill). The request that
        discovers that must leave the application exactly as it was, not
        half-transitioned into ``SUBMITTING`` with an idempotency key burned
        for nothing.
        """
        application = await _create_and_prepare(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        user_id = await _user_id(client, api, auth)
        new_hash = await advance_to_waiting_for_approval(
            session,
            user_id=user_id,
            application_id=uuid.UUID(application["id"]),  # type: ignore[arg-type]
        )
        await session.commit()  # type: ignore[attr-defined]

        approved = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/approve",
            headers=auth,
            json={"form_state_hash": new_hash},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["state"] == ApplicationState.WAITING_FOR_APPROVAL.value

        review = await client.get(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/review", headers=auth
        )
        assert review.json()["decision"]["allowed"] is True
        assert review.json()["decision"]["blockers"] == []

        submit = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": new_hash},
        )
        assert submit.status_code == 404
        assert submit.json()["error"]["code"] == "BROWSER_SESSION_NOT_FOUND"

        # Nothing was left half-done: the attempt never reached the point of no
        # return, so nothing needed to roll back in the first place.
        after = await client.get(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}", headers=auth
        )
        body = after.json()
        assert body["state"] == ApplicationState.WAITING_FOR_APPROVAL.value
        assert body["submission_attempted_at"] is None
        assert body["submitted_at"] is None

    async def test_a_second_attempt_fails_the_same_way_not_worse(
        self, client: object, session: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        """Because the first attempt never reached the point of no return, a
        retry is not 'already submitted'."""
        application = await _create_and_prepare(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        user_id = await _user_id(client, api, auth)
        new_hash = await advance_to_waiting_for_approval(
            session,
            user_id=user_id,
            application_id=uuid.UUID(application["id"]),  # type: ignore[arg-type]
        )
        await session.commit()  # type: ignore[attr-defined]
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/approve",
            headers=auth,
            json={"form_state_hash": new_hash},
        )

        first = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": new_hash},
        )
        second = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": new_hash},
        )
        assert first.json()["error"]["code"] == "BROWSER_SESSION_NOT_FOUND"
        assert second.json()["error"]["code"] == "BROWSER_SESSION_NOT_FOUND"


class TestApprovalInvalidation:
    async def test_answering_a_field_after_approval_revokes_it(
        self, client: object, session: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        """Safety requirement 3, end to end: a changed form voids a given approval."""
        application = await _create_and_prepare(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        user_id = await _user_id(client, api, auth)
        first_hash = await advance_to_waiting_for_approval(
            session,
            user_id=user_id,
            application_id=uuid.UUID(application["id"]),  # type: ignore[arg-type]
        )
        await session.commit()  # type: ignore[attr-defined]

        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/approve",
            headers=auth,
            json={"form_state_hash": first_hash},
        )

        changed = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/answer",
            headers=auth,
            json={"field_key": "cover_letter", "value": "A rewritten cover letter."},
        )
        assert changed.status_code == 200
        second_hash = changed.json()["form_state_hash"]
        assert second_hash != first_hash

        # The old approval no longer satisfies the gate, under the old or new hash.
        review = await client.get(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/review", headers=auth
        )
        assert review.json()["decision"]["allowed"] is False
        assert "NO_APPROVAL" in [b["code"] for b in review.json()["decision"]["blockers"]]

        stale_submit = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": first_hash},
        )
        assert stale_submit.status_code == 409
        assert stale_submit.json()["error"]["code"] == "APPROVAL_STALE"


class TestManualSubmission:
    """The honest stand-in while the browser agent does not exist yet."""

    async def test_recording_a_manual_submission_marks_it_unverified(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        application = await _create_and_prepare(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/record-manual-submission",
            headers=auth,
            json={"confirm": True, "note": "Applied directly on their careers page."},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == ApplicationState.SUBMITTED.value
        assert body["submission_evidence"]["verified_by_careerpilot"] is False

    async def test_requires_explicit_confirmation(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        application = await _create_and_prepare(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/record-manual-submission",
            headers=auth,
            json={"confirm": False},
        )
        assert response.status_code == 422

    async def test_cannot_be_recorded_twice(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        application = await _create_and_prepare(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/record-manual-submission",
            headers=auth,
            json={"confirm": True},
        )
        second = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/record-manual-submission",
            headers=auth,
            json={"confirm": True},
        )
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "ALREADY_SUBMITTED"
