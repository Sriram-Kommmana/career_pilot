"""Phase 6: the real submission click, exercised through the full HTTP API
against real, injected browser sessions and sanitized local fixtures.

No automated test here — or anywhere else in this project — ever submits to a
real employer site: every fixture's ``onsubmit`` returns ``false`` and
``page.set_content()`` pages have no server to receive a real POST in the
first place. ``window.__submitAttempts`` is incremented once per real click
reaching a fixture's submit handler, which is what every test below asserts
on directly, rather than trusting a status code alone — the explicit
instruction this module was written against is to assert the exact number of
submission clicks, not merely the returned HTTP status.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.browser import policy as browser_fill_policy
from app.browser.policy import OriginDecision
from app.browser.session import manager as session_manager
from app.core.config import get_settings
from app.domain.states import ApplicationState
from tests.browser.conftest import fixture_html
from tests.integration._browser_helpers import (
    advance_application_to_ready,
    advance_application_to_waiting_for_approval,
    user_id_from_auth,
)

pytestmark = [pytest.mark.integration, pytest.mark.browser]

try:
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover - exercised via the skip path below
    async_playwright = None  # type: ignore[assignment]

_SKIP_REASON = (
    "Playwright/Chromium is not available for browser tests.\n"
    "Install with:\n"
    '    pip install -e ".[browser]"\n'
    "    python -m playwright install chromium"
)


async def _submit_attempts(user_id: uuid.UUID, application_id: uuid.UUID) -> int:
    session = await session_manager.find_for_application(
        user_id=user_id, application_id=application_id
    )
    assert session is not None, "the browser session was unexpectedly closed"
    count = await session.page.evaluate("window.__submitAttempts || 0")  # type: ignore[attr-defined]
    return int(count)


@pytest.fixture(autouse=True)
def _skip_without_playwright() -> None:
    if async_playwright is None:
        pytest.skip(_SKIP_REASON)


class TestSuccessfulSubmission:
    async def test_an_approved_application_is_submitted_with_exactly_one_click(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        detail = await advance_application_to_waiting_for_approval(
            client,
            api,
            auth,
            seeded_job.id,  # type: ignore[attr-defined]
            monkeypatch,
            fixture_name="greenhouse_submission_ready_success.html",
        )
        approved = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/approve",
            headers=auth,
            json={"form_state_hash": detail["form_state_hash"]},
        )
        assert approved.status_code == 200, approved.text

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == ApplicationState.SUBMITTED.value
        assert body["submitted_at"] is not None
        assert body["submission_evidence"]["outcome"] == "CONFIRMED_SUCCESS"
        assert body["submission_evidence"]["verified_by_careerpilot"] is True

        user_id = user_id_from_auth(auth)
        assert await _submit_attempts(user_id, uuid.UUID(detail["id"])) == 1


class TestConfirmedFailureAndUncertainOutcomes:
    async def test_a_recognised_rejection_is_confirmed_failure_and_one_shot(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        detail = await advance_application_to_waiting_for_approval(
            client,
            api,
            auth,
            seeded_job.id,  # type: ignore[attr-defined]
            monkeypatch,
            fixture_name="greenhouse_submission_ready_rejected.html",
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/approve",
            headers=auth,
            json={"form_state_hash": detail["form_state_hash"]},
        )

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == ApplicationState.FAILED.value
        assert body["submission_evidence"]["outcome"] == "CONFIRMED_FAILURE"
        assert body["failure_reason"]

        user_id = user_id_from_auth(auth)
        application_id = uuid.UUID(detail["id"])
        assert await _submit_attempts(user_id, application_id) == 1

        # One-shot: a confirmed failure still consumed the claimed attempt.
        # Retrying requires re-preparing, not a second click here.
        second = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        assert second.status_code == 409
        codes = {b["code"] for b in second.json()["error"]["details"]["blockers"]}
        assert "ALREADY_SUBMITTED" in codes
        assert await _submit_attempts(user_id, application_id) == 1

    async def test_no_recognisable_signal_is_uncertain_and_never_auto_retried(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        detail = await advance_application_to_waiting_for_approval(
            client,
            api,
            auth,
            seeded_job.id,  # type: ignore[attr-defined]
            monkeypatch,
            fixture_name="greenhouse_submission_ready_ambiguous.html",
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/approve",
            headers=auth,
            json={"form_state_hash": detail["form_state_hash"]},
        )

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == ApplicationState.SUBMITTED_UNCERTAIN.value
        assert body["submission_evidence"]["outcome"] == "UNCERTAIN"

        user_id = user_id_from_auth(auth)
        application_id = uuid.UUID(detail["id"])
        assert await _submit_attempts(user_id, application_id) == 1

        # Never automatically retried: a second call is refused, not re-clicked.
        second = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        assert second.status_code == 409
        assert await _submit_attempts(user_id, application_id) == 1


class TestRefusalsBeforeAnyClick:
    async def test_refused_without_approval_and_no_click_happens(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        detail = await advance_application_to_waiting_for_approval(
            client, api, auth, seeded_job.id, monkeypatch  # type: ignore[attr-defined]
        )
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "SUBMISSION_BLOCKED"
        codes = {b["code"] for b in response.json()["error"]["details"]["blockers"]}
        assert "NO_APPROVAL" in codes

        user_id = user_id_from_auth(auth)
        assert await _submit_attempts(user_id, uuid.UUID(detail["id"])) == 0

    async def test_a_stale_submitted_hash_is_refused_before_the_browser_is_touched(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        detail = await advance_application_to_waiting_for_approval(
            client, api, auth, seeded_job.id, monkeypatch  # type: ignore[attr-defined]
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/approve",
            headers=auth,
            json={"form_state_hash": detail["form_state_hash"]},
        )
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": "0" * 64},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "APPROVAL_STALE"

        user_id = user_id_from_auth(auth)
        assert await _submit_attempts(user_id, uuid.UUID(detail["id"])) == 0

    async def test_a_missing_required_field_and_an_unresolved_legal_question_both_refuse(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``greenhouse_standard.html`` has no field at all for resume, work
        authorisation, or visa sponsorship (so they are never actually
        verified against a live page, however confirmed they are in our own
        database) *and* a required legal question with no possible
        authoritative source (so it is left ``NEEDS_HUMAN``) — covering both
        "a required field is missing" and "a sensitive/legal question is
        unresolved" in the one realistic fixture that produces both."""
        application = await advance_application_to_ready(
            client, api, auth, seeded_job.id  # type: ignore[attr-defined]
        )
        user_id = user_id_from_auth(auth)
        application_id = uuid.UUID(application["id"])
        settings = get_settings()

        session = await session_manager.create(
            user_id=user_id,
            application_id=application_id,
            adapter_name="greenhouse",
            headless=True,
            timeout_ms=settings.browser_timeout_ms,
            ttl_seconds=settings.browser_session_ttl_seconds,
        )
        await session.page.set_content(fixture_html("greenhouse_standard.html"))  # type: ignore[attr-defined]
        monkeypatch.setattr(
            browser_fill_policy,
            "classify_origin",
            lambda url: OriginDecision(
                url=url, allowed=True, origin="https://job-boards.greenhouse.io"
            ),
        )
        from app.services import browser_submit as browser_submit_service

        monkeypatch.setattr(browser_submit_service, "_same_application_page", lambda *_a: True)

        inspected = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/browser-session/inspect", headers=auth
        )
        assert inspected.status_code == 200, inspected.text
        filled = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/browser-session/fill",
            headers=auth,
            json={"mapping_token": inspected.json()["mapping_token"]},
        )
        assert filled.status_code == 200, filled.text
        assert filled.json()["application_state"] == "WAITING_FOR_USER_INPUT"

        detail = (
            await client.get(f"{api}/applications/{application_id}", headers=auth)  # type: ignore[attr-defined]
        ).json()

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        assert response.status_code == 409
        codes = {b["code"] for b in response.json()["error"]["details"]["blockers"]}
        # The legal question is a browser-extracted custom question, not a
        # baseline ApplicationField row, so it surfaces as the application's
        # own unresolved NEEDS_HUMAN intervention (set by browser_fill.fill)
        # rather than the gate's per-field FIELDS_NEED_HUMAN blocker — still
        # a hard refusal either way.
        assert "UNRESOLVED_REQUIRED_FIELDS" in codes
        assert "UNRESOLVED_INTERVENTION" in codes
        assert "WRONG_STATE" in codes
        assert await _submit_attempts(user_id, application_id) == 0


class TestRefusalsBoundToTheBrowserItself:
    async def test_a_session_that_was_never_inspected_refuses(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An approval is bound to a session that actually reviewed the page
        — a freshly opened session (even if one happens to be open for this
        application) has no ``last_inspection``/``last_mapping`` yet."""
        application = await advance_application_to_ready(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        user_id = user_id_from_auth(auth)
        application_id = uuid.UUID(application["id"])
        settings = get_settings()
        session = await session_manager.create(
            user_id=user_id,
            application_id=application_id,
            adapter_name="greenhouse",
            headless=True,
            timeout_ms=settings.browser_timeout_ms,
            ttl_seconds=settings.browser_session_ttl_seconds,
        )
        await session.page.set_content(  # type: ignore[attr-defined]
            fixture_html("greenhouse_submission_ready_ambiguous.html")
        )
        monkeypatch.setattr(
            browser_fill_policy,
            "classify_origin",
            lambda url: OriginDecision(
                url=url, allowed=True, origin="https://job-boards.greenhouse.io"
            ),
        )
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": application["form_state_hash"]},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "BROWSER_SESSION_NOT_INSPECTED"

    async def test_a_disconnected_browser_refuses_rather_than_pretending_a_click_happened(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        detail = await advance_application_to_waiting_for_approval(
            client, api, auth, seeded_job.id, monkeypatch  # type: ignore[attr-defined]
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/approve",
            headers=auth,
            json={"form_state_hash": detail["form_state_hash"]},
        )
        user_id = user_id_from_auth(auth)
        application_id = uuid.UUID(detail["id"])
        live = await session_manager.find_for_application(
            user_id=user_id, application_id=application_id
        )
        assert live is not None
        # Simulates a browser crash/disconnect.
        await live.page.close()  # type: ignore[attr-defined]

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "BROWSER_SESSION_EXPIRED"

        after = (
            await client.get(f"{api}/applications/{application_id}", headers=auth)  # type: ignore[attr-defined]
        ).json()
        assert after["submission_attempted_at"] is None

    async def test_navigating_to_an_unsupported_origin_refuses(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Restores the *real*, unpatched origin check for the submit call
        itself — a ``page.set_content()`` page's url is ``about:blank``,
        which genuinely is not a supported origin, proving this is the real
        check running, not a bypass left on by accident."""
        real_classify_origin = browser_fill_policy.classify_origin
        detail = await advance_application_to_waiting_for_approval(
            client, api, auth, seeded_job.id, monkeypatch  # type: ignore[attr-defined]
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/approve",
            headers=auth,
            json={"form_state_hash": detail["form_state_hash"]},
        )
        monkeypatch.setattr(browser_fill_policy, "classify_origin", real_classify_origin)

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "UNSUPPORTED_ORIGIN"

        user_id = user_id_from_auth(auth)
        assert await _submit_attempts(user_id, uuid.UUID(detail["id"])) == 0

    async def test_a_different_user_gets_the_same_not_found_as_a_missing_session(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        second_auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        detail = await advance_application_to_waiting_for_approval(
            client, api, auth, seeded_job.id, monkeypatch  # type: ignore[attr-defined]
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/approve",
            headers=auth,
            json={"form_state_hash": detail["form_state_hash"]},
        )
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/submit",
            headers=second_auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        # Ownership is enforced by get_application (404) before browser_submit
        # is ever reached for a user who does not own this application.
        assert response.status_code == 404

    async def test_approval_is_invalidated_when_the_live_page_drifts_from_what_was_approved(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Simulates a human typing something different into the *visible*
        browser after approving — the database never changed, so only a
        live re-read catches this. The invalidation itself must survive even
        though this call refuses."""
        detail = await advance_application_to_waiting_for_approval(
            client, api, auth, seeded_job.id, monkeypatch  # type: ignore[attr-defined]
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/approve",
            headers=auth,
            json={"form_state_hash": detail["form_state_hash"]},
        )
        user_id = user_id_from_auth(auth)
        application_id = uuid.UUID(detail["id"])
        live = await session_manager.find_for_application(
            user_id=user_id, application_id=application_id
        )
        assert live is not None
        await live.page.fill("#first_name", "SomeoneElse")  # type: ignore[attr-defined]

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "APPROVAL_STALE"
        assert await _submit_attempts(user_id, application_id) == 0

        # The invalidation really was persisted, not just reported: the review
        # screen now shows no active approval for the current form state.
        review = (
            await client.get(f"{api}/applications/{application_id}/review", headers=auth)  # type: ignore[attr-defined]
        ).json()
        assert review["decision"]["allowed"] is False
        assert "NO_APPROVAL" in {b["code"] for b in review["decision"]["blockers"]}


class TestDuplicateAndConcurrentSubmission:
    async def test_concurrent_submission_requests_only_one_proceeds(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        detail = await advance_application_to_waiting_for_approval(
            client,
            api,
            auth,
            seeded_job.id,  # type: ignore[attr-defined]
            monkeypatch,
            fixture_name="greenhouse_submission_ready_ambiguous.html",
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/approve",
            headers=auth,
            json={"form_state_hash": detail["form_state_hash"]},
        )

        async def _submit() -> object:
            return await client.post(  # type: ignore[attr-defined]
                f"{api}/applications/{detail['id']}/submit",
                headers=auth,
                json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
            )

        first, second = await asyncio.gather(_submit(), _submit())
        statuses = sorted([first.status_code, second.status_code])  # type: ignore[attr-defined]
        assert statuses == [200, 409]

        user_id = user_id_from_auth(auth)
        application_id = uuid.UUID(detail["id"])
        assert await _submit_attempts(user_id, application_id) == 1

        after = (
            await client.get(f"{api}/applications/{application_id}", headers=auth)  # type: ignore[attr-defined]
        ).json()
        assert after["state"] == ApplicationState.SUBMITTED_UNCERTAIN.value


class TestUncertainOutcomeReconciliation:
    async def test_reconciliation_confirms_success_when_the_form_is_gone_without_clicking_again(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        detail = await advance_application_to_waiting_for_approval(
            client,
            api,
            auth,
            seeded_job.id,  # type: ignore[attr-defined]
            monkeypatch,
            fixture_name="greenhouse_submission_ready_ambiguous.html",
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/approve",
            headers=auth,
            json={"form_state_hash": detail["form_state_hash"]},
        )
        submitted = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        assert submitted.json()["state"] == ApplicationState.SUBMITTED_UNCERTAIN.value

        user_id = user_id_from_auth(auth)
        application_id = uuid.UUID(detail["id"])
        # Corroborated: new marker AND the application form has disappeared.
        # The confirmation genuinely appears a little later than our bounded
        # wait — a realistic reason an attempt can be uncertain even though
        # the employer really did receive it.
        live = await session_manager.find_for_application(
            user_id=user_id, application_id=application_id
        )
        assert live is not None
        await live.page.evaluate(  # type: ignore[attr-defined]
            "document.querySelector('form').remove();"
            "document.body.insertAdjacentHTML('beforeend', "
            "'<div>Thank you for applying! We have received your application.</div>')"
        )

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/reconcile-submission",
            headers=auth,
            json={"outcome": "SUBMITTED", "note": "Confirmed via the still-open browser."},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == ApplicationState.SUBMITTED.value
        assert body["submission_evidence"]["checked_live_page"] is True
        assert body["submission_evidence"]["verified_by_careerpilot"] is True

        # Reconciliation never clicks the submit control again.
        assert await _submit_attempts(user_id, application_id) == 1

    async def test_reconciliation_does_not_verify_a_new_marker_while_the_form_remains(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        detail = await advance_application_to_waiting_for_approval(
            client,
            api,
            auth,
            seeded_job.id,  # type: ignore[attr-defined]
            monkeypatch,
            fixture_name="greenhouse_submission_ready_ambiguous.html",
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/approve",
            headers=auth,
            json={"form_state_hash": detail["form_state_hash"]},
        )
        submitted = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        assert submitted.json()["state"] == ApplicationState.SUBMITTED_UNCERTAIN.value

        user_id = user_id_from_auth(auth)
        application_id = uuid.UUID(detail["id"])
        # Uncorroborated: a new marker appeared but the same URL and the form is still present.
        # The confirmation genuinely appears a little later than our bounded
        # wait — a realistic reason an attempt can be uncertain even though
        # the employer really did receive it.
        live = await session_manager.find_for_application(
            user_id=user_id, application_id=application_id
        )
        assert live is not None
        await live.page.evaluate(  # type: ignore[attr-defined]
            "document.body.insertAdjacentHTML('beforeend', "
            "'<div>Thank you for applying! We have received your application.</div>')"
        )

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/reconcile-submission",
            headers=auth,
            json={"outcome": "SUBMITTED", "note": "Confirmed via the still-open browser."},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        # The user's own attestation still stands, separate from our verification.
        assert body["state"] == ApplicationState.SUBMITTED.value
        assert body["submission_evidence"]["checked_live_page"] is False
        assert body["submission_evidence"]["verified_by_careerpilot"] is False

        # Reconciliation never clicks the submit control again.
        assert await _submit_attempts(user_id, application_id) == 1

    async def test_reconciliation_does_not_treat_pre_attempt_content_as_new_evidence(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Adversarial regression: a success-marker phrase was already on the
        page *before* the submit click ever happened (unrelated testimonial
        copy, baked into the fixture from page load). Reconciliation on this
        same still-open session must not treat that stale content as
        corroborating evidence — it is not new since the baseline captured
        immediately before the click."""
        detail = await advance_application_to_waiting_for_approval(
            client,
            api,
            auth,
            seeded_job.id,  # type: ignore[attr-defined]
            monkeypatch,
            fixture_name="greenhouse_submission_ready_stale_marker.html",
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/approve",
            headers=auth,
            json={"form_state_hash": detail["form_state_hash"]},
        )
        submitted = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        assert submitted.json()["state"] == ApplicationState.SUBMITTED_UNCERTAIN.value

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/reconcile-submission",
            headers=auth,
            json={"outcome": "SUBMITTED", "note": "I checked myself."},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == ApplicationState.SUBMITTED.value
        # The outcome is still exactly what the user attested to (SUBMITTED)
        # — that path is never blocked — but CareerPilot's own passive check
        # must not have corroborated it from stale content.
        assert body["submission_evidence"]["checked_live_page"] is False
        assert body["submission_evidence"]["verified_by_careerpilot"] is False

        user_id = user_id_from_auth(auth)
        assert await _submit_attempts(user_id, uuid.UUID(detail["id"])) == 1

    async def test_reconciliation_on_a_reopened_session_requires_the_form_to_be_gone(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Adversarial regression: the original session (with its baseline)
        is gone, a new session is opened on a page that happens to contain a
        success-marker phrase, but the application form is still fully
        visible and unchanged — not itself a sign that anything was
        confirmed. With no baseline available, reconciliation falls back to
        requiring the form to be gone, and must refuse to corroborate here."""
        detail = await advance_application_to_waiting_for_approval(
            client,
            api,
            auth,
            seeded_job.id,  # type: ignore[attr-defined]
            monkeypatch,
            fixture_name="greenhouse_submission_ready_ambiguous.html",
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/approve",
            headers=auth,
            json={"form_state_hash": detail["form_state_hash"]},
        )
        submitted = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        assert submitted.json()["state"] == ApplicationState.SUBMITTED_UNCERTAIN.value

        user_id = user_id_from_auth(auth)
        application_id = uuid.UUID(detail["id"])
        settings = get_settings()

        # Close the original session (its baseline goes with it) and open a
        # fresh one pointed at a page containing a success-marker phrase with
        # the form still fully present -- no corroborating structural change.
        await client.delete(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/browser-session", headers=auth
        )
        new_session = await session_manager.create(
            user_id=user_id,
            application_id=application_id,
            adapter_name="greenhouse",
            headless=True,
            timeout_ms=settings.browser_timeout_ms,
            ttl_seconds=settings.browser_session_ttl_seconds,
        )
        await new_session.page.set_content(  # type: ignore[attr-defined]
            fixture_html("greenhouse_submission_preexisting_success_marker.html")
        )

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/reconcile-submission",
            headers=auth,
            json={"outcome": "SUBMITTED", "note": "I checked myself."},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["submission_evidence"]["checked_live_page"] is False
        assert body["submission_evidence"]["verified_by_careerpilot"] is False

        await session_manager.close(new_session.session_id)

    async def test_reconciliation_accepts_the_users_own_word_once_the_session_is_closed(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        detail = await advance_application_to_waiting_for_approval(
            client,
            api,
            auth,
            seeded_job.id,  # type: ignore[attr-defined]
            monkeypatch,
            fixture_name="greenhouse_submission_ready_ambiguous.html",
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/approve",
            headers=auth,
            json={"form_state_hash": detail["form_state_hash"]},
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        await client.delete(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/browser-session", headers=auth
        )

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/reconcile-submission",
            headers=auth,
            json={"outcome": "FAILED", "note": "Checked the employer's site myself: not received."},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == ApplicationState.FAILED.value
        assert body["submission_evidence"]["checked_live_page"] is False
        assert body["submission_evidence"]["verified_by_careerpilot"] is False

    async def test_reconciliation_never_resubmits_and_is_refused_once_already_resolved(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        detail = await advance_application_to_waiting_for_approval(
            client,
            api,
            auth,
            seeded_job.id,  # type: ignore[attr-defined]
            monkeypatch,
            fixture_name="greenhouse_submission_ready_success.html",
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/approve",
            headers=auth,
            json={"form_state_hash": detail["form_state_hash"]},
        )
        submitted = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        assert submitted.json()["state"] == ApplicationState.SUBMITTED.value

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/reconcile-submission",
            headers=auth,
            json={"outcome": "SUBMITTED"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_STATE_TRANSITION"

        user_id = user_id_from_auth(auth)
        assert await _submit_attempts(user_id, uuid.UUID(detail["id"])) == 1


class TestBrowserCrashAfterTheClick:
    async def test_an_error_during_post_click_observation_is_recorded_as_uncertain(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A real click happens — proving the submit control really was
        activated — but the page then becomes unreadable (simulating a
        disconnect between the click and confirmation). The outcome must be
        recorded as uncertain, never silently dropped and never guessed into
        a false success."""
        detail = await advance_application_to_waiting_for_approval(
            client,
            api,
            auth,
            seeded_job.id,  # type: ignore[attr-defined]
            monkeypatch,
            fixture_name="greenhouse_submission_ready_ambiguous.html",
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/approve",
            headers=auth,
            json={"form_state_hash": detail["form_state_hash"]},
        )

        from app.services import browser_submit as browser_submit_service

        adapter = browser_submit_service._ADAPTERS["greenhouse"]

        async def _click_then_crash(
            page: object, control: object, *, confirmation_timeout_ms: int
        ) -> None:
            await control.click()  # type: ignore[attr-defined]
            raise RuntimeError("simulated browser crash during post-click observation")

        monkeypatch.setattr(adapter, "submit", _click_then_crash)

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{detail['id']}/submit",
            headers=auth,
            json={"confirm": True, "form_state_hash": detail["form_state_hash"]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == ApplicationState.SUBMITTED_UNCERTAIN.value
        assert body["submission_evidence"]["outcome"] == "UNCERTAIN"

        user_id = user_id_from_auth(auth)
        assert await _submit_attempts(user_id, uuid.UUID(detail["id"])) == 1
