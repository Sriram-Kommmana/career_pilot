"""Shared setup for the Phase 5 integration tests.

Not a ``test_*`` module itself — a helper module the browser-session and
fill-verification integration tests both import, so the "get an application
to READY, with a real resume and a real confirmed profile" dance is written
once.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.config import get_settings
from app.core.security import decode_access_token


def user_id_from_auth(auth: dict[str, str]) -> UUID:
    """The real user id behind an ``auth`` fixture's bearer token.

    ``app.browser.session.BrowserSessionManager`` is keyed by user id, not by
    a token, so tests that inject a session directly (bypassing the live
    navigation ``POST /browser-session`` needs) need the real id.
    """
    token = auth["Authorization"].removeprefix("Bearer ").strip()
    settings = get_settings()
    return decode_access_token(
        token, secret_key=settings.secret_key, algorithm=settings.jwt_algorithm
    )


async def advance_application_to_ready(
    client: object, api: str, auth: dict[str, str], job_id: object
) -> dict[str, Any]:
    """Create an application and push it to ``READY`` through the real HTTP API.

    Fills every baseline-required field from a confirmed profile (full name as
    two words, so the Greenhouse adapter's first/last-name split applies) plus
    a real uploaded resume, exactly as a user would before ever opening a
    browser session.
    """
    patch = await client.patch(  # type: ignore[attr-defined]
        f"{api}/profile",
        headers=auth,
        json={
            "full_name": "Jordan Lee",
            "linkedin_url": "https://linkedin.com/in/jordanlee",
            "work_authorisation": {"authorised": "Yes", "requires_sponsorship": "No"},
        },
    )
    assert patch.status_code == 200, patch.text

    upload = await client.post(  # type: ignore[attr-defined]
        f"{api}/resumes",
        headers=auth,
        files={"file": ("resume.txt", b"Jordan Lee. Backend engineer.", "text/plain")},
    )
    assert upload.status_code == 201, upload.text

    created = await client.post(  # type: ignore[attr-defined]
        f"{api}/applications", headers=auth, json={"job_id": str(job_id)}
    )
    assert created.status_code == 201, created.text
    application = created.json()

    prepared = await client.post(  # type: ignore[attr-defined]
        f"{api}/applications/{application['id']}/prepare", headers=auth
    )
    assert prepared.status_code == 200, prepared.text
    body: dict[str, Any] = prepared.json()
    assert body["state"] == "READY", body
    return body


async def advance_application_to_waiting_for_approval(
    client: object,
    api: str,
    auth: dict[str, str],
    job_id: object,
    monkeypatch: Any,
    *,
    fixture_name: str = "greenhouse_submission_ready_ambiguous.html",
) -> dict[str, Any]:
    """Push an application all the way to ``WAITING_FOR_APPROVAL`` through the
    real HTTP API and a real, injected browser session — the same
    inspect -> review -> fill -> verify pipeline described in
    ``docs/BROWSER.md`` — using a fixture whose every baseline-required field
    (name, email, resume, work authorisation, visa sponsorship) actually has
    a matching, fillable field on the page, unlike ``greenhouse_standard.html``
    (see that fixture's own module docstring in
    ``test_browser_fill_verification.py`` for why it deliberately does not).

    Leaves a real, open ``BrowserSession`` for this application — Phase 6's
    submission tests need it still open to drive the actual click.

    The resume field is never auto-filled (file uploads never are — see
    ``app.browser.mapping``'s file-kind guard), so this attaches a file to it
    directly, exactly as a human would in the visible browser, *before*
    inspecting/filling — the second pass in ``browser_fill.fill`` is what
    notices a human did this and records it as verified.
    """
    import tempfile
    from pathlib import Path

    from app.browser import policy as browser_fill_policy
    from app.browser.policy import OriginDecision
    from app.browser.session import manager as session_manager
    from app.core.config import get_settings
    from tests.browser.conftest import fixture_html

    application = await advance_application_to_ready(client, api, auth, job_id)
    user_id = user_id_from_auth(auth)
    application_id = UUID(application["id"])
    settings = get_settings()

    session = await session_manager.create(
        user_id=user_id,
        application_id=application_id,
        adapter_name="greenhouse",
        headless=True,
        timeout_ms=settings.browser_timeout_ms,
        ttl_seconds=settings.browser_session_ttl_seconds,
    )
    await session.page.set_content(fixture_html(fixture_name))  # type: ignore[attr-defined]

    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
        handle.write(b"Jordan Lee. Backend engineer.")
        resume_path = handle.name
    try:
        await session.page.set_input_files("#resume", resume_path)  # type: ignore[attr-defined]
    finally:
        Path(resume_path).unlink(missing_ok=True)  # noqa: ASYNC240 - tiny test cleanup

    monkeypatch.setattr(
        browser_fill_policy,
        "classify_origin",
        lambda url: OriginDecision(
            url=url, allowed=True, origin="https://job-boards.greenhouse.io"
        ),
    )
    # A page.set_content() page's url is always "about:blank", which can never
    # equal a real application_url — bypassed here the same way origin is,
    # for the same reason. test_browser_submission.py's
    # TestRefusalsBoundToTheBrowserItself::test_navigating_to_an_unsupported_origin_refuses
    # and a dedicated same-page test restore the real checks to prove they work.
    from app.services import browser_submit as browser_submit_service

    monkeypatch.setattr(browser_submit_service, "_same_application_page", lambda *_args: True)

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
    assert report["application_state"] == "WAITING_FOR_APPROVAL", report
    assert report["verification_status"] == "PASSED", report

    detail = await client.get(  # type: ignore[attr-defined]
        f"{api}/applications/{application_id}", headers=auth
    )
    assert detail.status_code == 200
    return detail.json()  # type: ignore[no-any-return]
