"""The 5C integration: a real browser fill+verify, feeding the *existing,
unchanged* submission gate real data for the first time.

``app.browser.policy.classify_origin`` is monkeypatched to allow the current
page for this module only. That origin check already has its own exhaustive
suite (``tests/unit/test_browser_policy.py``, 26 cases covering file:/
javascript:/data:/local-network/unsupported-domain rejection) — nothing here
is weakening or re-testing it. The real, un-patched check is what every other
test in this repository runs against; a local Playwright page can never pass
it for real (``_is_local_or_private`` rejects a local test server even if an
origin were added to the allow-list, which is itself the point of that
check). This module's own job is the part nothing else exercises: does a real
browser-verified ``ApplicationField``/``Application`` row correctly drive
``applications.transition()`` and the submission gate.

The chosen fixture (``greenhouse_standard.html``) has no field at all for
work authorisation, visa sponsorship, or a resume upload — a real limitation
of this phase's Greenhouse adapter (``docs/BROWSER.md``), not a test
convenience. That makes the realistic outcome here a *refusal*, not a clean
pass: those are required fields, confirmed in CareerPilot's own database from
the user's profile, but never actually verified against the live page, so the
gate must keep refusing. Proving that refusal is the point of this test, not
a gap in it — "resolved in our database" and "verified on the employer's
actual page" are different things, and conflating them is exactly what
``docs/SAFETY.md`` forbids.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from app.browser import policy as browser_fill_policy
from app.browser.policy import OriginDecision
from app.browser.session import BrowserSession
from app.browser.session import manager as session_manager
from app.core.config import get_settings
from tests.browser.conftest import fixture_html
from tests.integration._browser_helpers import advance_application_to_ready, user_id_from_auth

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


@pytest.fixture
async def ready_application_with_session(
    client: object, api: str, auth: dict[str, str], seeded_job: object
) -> AsyncIterator[tuple[dict[str, object], BrowserSession]]:
    """A ``READY`` application plus a real browser session pointed at the
    sanitized Greenhouse fixture. Mirrors ``injected_session`` in
    ``test_browser_session.py`` — see that module's docstring for why the
    session is injected directly rather than created through ``POST
    /browser-session`` (which requires navigating to a real supported
    origin)."""
    if async_playwright is None:
        pytest.skip(_SKIP_REASON)

    application = await advance_application_to_ready(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
    user_id = user_id_from_auth(auth)
    application_id = uuid.UUID(application["id"])
    settings = get_settings()
    try:
        session = await session_manager.create(
            user_id=user_id,
            application_id=application_id,
            adapter_name="greenhouse",
            headless=True,
            timeout_ms=settings.browser_timeout_ms,
            ttl_seconds=settings.browser_session_ttl_seconds,
        )
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"{_SKIP_REASON}\nLaunch error: {exc}")

    await session.page.set_content(fixture_html("greenhouse_standard.html"))  # type: ignore[attr-defined]
    try:
        yield application, session
    finally:
        await session_manager.close(session.session_id)


class TestFillVerifyAndGate:
    async def test_a_realistic_partial_form_is_filled_verified_and_then_refused(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
        ready_application_with_session: tuple[dict[str, object], BrowserSession],
    ) -> None:
        application, _session = ready_application_with_session
        application_id = application["id"]

        # See the module docstring: this isolates the fill/verify/gate wiring
        # from origin policy, which is tested exhaustively elsewhere and can
        # never honestly pass for a local-only fixture page.
        monkeypatch.setattr(
            browser_fill_policy,
            "classify_origin",
            lambda url: OriginDecision(
                url=url, allowed=True, origin="https://job-boards.greenhouse.io"
            ),
        )

        inspected = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/browser-session/inspect", headers=auth
        )
        assert inspected.status_code == 200, inspected.text
        mapping_token = inspected.json()["mapping_token"]

        filled = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/browser-session/fill",
            headers=auth,
            json={"mapping_token": mapping_token},
        )
        assert filled.status_code == 200, filled.text
        report = filled.json()

        outcomes = {o["field_id"]: o for o in report["outcomes"]}
        for filled_id in ("first_name", "last_name", "email", "question_1001"):
            assert outcomes[filled_id]["action_result"] == "filled", outcomes[filled_id]
            assert outcomes[filled_id]["verification"] == "PASSED", outcomes[filled_id]
        # The legal question has no confirmed source and nobody typed into it
        # in this headless test run: honestly reported as never filled, not
        # guessed and not silently dropped.
        assert "question_1003" not in outcomes

        # Required fields this fixture has no field for at all (work
        # authorisation, visa sponsorship, a resume) are never touched by the
        # browser, so they are never actually verified — only "resolved" in
        # our own database from the user's confirmed profile. The gate must
        # tell those apart.
        assert report["verification_status"] == "FAILED"
        assert report["application_state"] == "WAITING_FOR_USER_INPUT"

        get_response = await client.get(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}", headers=auth
        )
        detail = get_response.json()
        fields_by_key = {f["field_key"]: f for f in detail["fields"]}
        for untouched_key in ("work_authorisation", "requires_sponsorship", "resume"):
            assert fields_by_key[untouched_key]["verification"] == "NOT_ATTEMPTED", (
                untouched_key,
                fields_by_key[untouched_key],
            )

        review = await client.get(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/review", headers=auth
        )
        assert review.status_code == 200
        decision = review.json()["decision"]
        assert decision["allowed"] is False
        codes = {b["code"] for b in decision["blockers"]}
        assert "VERIFICATION_FAILED" in codes
        assert "WRONG_STATE" in codes

        approve = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/approve",
            headers=auth,
            json={"form_state_hash": detail["form_state_hash"]},
        )
        assert approve.status_code == 409
        assert approve.json()["error"]["code"] == "INVALID_STATE_TRANSITION"


class TestVerificationNoteNeverLeaksAValue:
    """Regression test: Playwright's own retry/call-log embeds the attempted
    value once a locator resolves but the fill action keeps retrying (e.g. a
    disabled field) — confirmed by direct reproduction against a real
    browser during this review. Chromium itself partially masks a
    ``type="tel"`` input's value in that log (only the last few characters
    were visible in reproduction), which is not something this project
    controls or can rely on for every field type — ``verification_note``
    must never carry *any* of that raw exception text, for a field whose
    confirmed value is exactly the kind of thing ``docs/SAFETY.md`` requires
    to be stored masked. Asserting an exact match against the fixed, generic
    message (rather than "the full sentinel isn't present") is what actually
    catches a partial leak like that one.
    """

    async def test_a_disabled_field_never_leaks_its_attempted_value(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        if async_playwright is None:
            pytest.skip(_SKIP_REASON)

        sentinel = "SENSITIVE-PHONE-VALUE-7788990011"
        application = await advance_application_to_ready(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        patched = await client.patch(  # type: ignore[attr-defined]
            f"{api}/profile", headers=auth, json={"phone": sentinel}
        )
        assert patched.status_code == 200, patched.text
        reprepared = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/prepare", headers=auth
        )
        assert reprepared.status_code == 200, reprepared.text
        assert reprepared.json()["state"] == "READY"

        user_id = user_id_from_auth(auth)
        application_id = uuid.UUID(application["id"])
        settings = get_settings()
        # A short timeout keeps the real Playwright retry loop (and the test)
        # fast while still giving it time to embed the value in its call log,
        # exactly as reproduced manually against a disabled input.
        session = await session_manager.create(
            user_id=user_id,
            application_id=application_id,
            adapter_name="greenhouse",
            headless=True,
            timeout_ms=1500,
            ttl_seconds=settings.browser_session_ttl_seconds,
        )
        try:
            await session.page.set_content(  # type: ignore[attr-defined]
                fixture_html("greenhouse_disabled_phone.html")
            )
            monkeypatch.setattr(
                browser_fill_policy,
                "classify_origin",
                lambda url: OriginDecision(
                    url=url, allowed=True, origin="https://job-boards.greenhouse.io"
                ),
            )

            inspected = await client.post(  # type: ignore[attr-defined]
                f"{api}/applications/{application_id}/browser-session/inspect", headers=auth
            )
            assert inspected.status_code == 200, inspected.text
            mapping_token = inspected.json()["mapping_token"]

            filled = await client.post(  # type: ignore[attr-defined]
                f"{api}/applications/{application_id}/browser-session/fill",
                headers=auth,
                json={"mapping_token": mapping_token},
            )
            assert filled.status_code == 200, filled.text
            report = filled.json()

            phone_outcome = next(o for o in report["outcomes"] if o["field_id"] == "phone")
            assert phone_outcome["action_result"] == "fill_failed"
            # An exact match, not just "the sentinel isn't present" — Chromium
            # itself partially masks a type="tel" value in its own call log
            # (confirmed by direct reproduction), so a substring-absence check
            # against the full sentinel would miss a partial leak of exactly
            # the kind a shorter or differently-formatted value could produce.
            assert phone_outcome["verification_note"] == "Could not fill or verify this field."
            assert sentinel not in filled.text

            detail = await client.get(  # type: ignore[attr-defined]
                f"{api}/applications/{application_id}", headers=auth
            )
            assert sentinel not in detail.text
            phone_field = next(f for f in detail.json()["fields"] if f["field_key"] == "phone")
            assert phone_field["verification_note"] == "Could not fill or verify this field."
        finally:
            await session_manager.close(session.session_id)
