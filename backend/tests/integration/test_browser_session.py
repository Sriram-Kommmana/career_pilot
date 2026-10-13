"""Phase 5's browser-session lifecycle: ownership, expiry, conflicts, and the
mapping-token staleness check — all through the real HTTP routes.

``POST /{id}/browser-session`` itself navigates a real browser to the
application's employer URL, which ``app.browser.policy`` only ever allows for
a real, supported employer origin (``docs/SAFETY.md`` section 6) — by design,
nothing short of a live Greenhouse page can pass that check, and this suite
must never depend on one (``docs/PLAN.md``'s Phase 5 testing rule). So these
tests inject a session directly into the same process-wide
``app.browser.session.manager`` singleton the API routes read from — exactly
the same technique ``tests/conftest.py``'s ``advance_to_waiting_for_approval``
already uses to stand in for a capability a plain HTTP call cannot reach yet —
then point the injected page at a local, sanitized fixture with
``page.set_content()``. ``app.browser.policy`` itself is exhaustively unit
tested in ``tests/unit/test_browser_policy.py``; this module is not retesting
that, only the session/ownership/staleness wiring built on top of it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from app.browser import policy as browser_fill_policy
from app.browser.policy import OriginDecision
from app.browser.session import BrowserSession
from app.browser.session import manager as session_manager
from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.errors import BrowserSessionConflictError, BrowserSessionExpiredError
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
async def injected_session(
    client: object, api: str, auth: dict[str, str], seeded_job: object
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID, BrowserSession]]:
    """A real ``BrowserSession`` registered in the process-wide manager,
    pointed at local fixture content, for a real application the caller owns
    (so ``inspect``/``fill`` have real ``ApplicationField``/``ApplicationAnswer``
    rows to build a mapping from). Standing in for the part of ``start_session``
    that a live-network-free test cannot exercise: the navigation itself."""
    if async_playwright is None:
        pytest.skip(_SKIP_REASON)

    application = await advance_application_to_ready(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
    user_id = user_id_from_auth(auth)
    application_id = uuid.UUID(application["id"])
    try:
        session = await session_manager.create(
            user_id=user_id,
            application_id=application_id,
            adapter_name="greenhouse",
            headless=True,
            timeout_ms=get_settings().browser_timeout_ms,
            ttl_seconds=get_settings().browser_session_ttl_seconds,
        )
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"{_SKIP_REASON}\nLaunch error: {exc}")

    try:
        yield user_id, application_id, session
    finally:
        await session_manager.close(session.session_id)


class TestNoSessionOpen:
    async def test_get_with_no_session_is_404(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        application = await advance_application_to_ready(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        response = await client.get(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/browser-session", headers=auth
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "BROWSER_SESSION_NOT_FOUND"

    async def test_delete_with_no_session_is_a_safe_no_op(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        application = await advance_application_to_ready(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        response = await client.delete(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/browser-session", headers=auth
        )
        assert response.status_code == 200

    async def test_inspect_with_no_session_is_404(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        application = await advance_application_to_ready(client, api, auth, seeded_job.id)  # type: ignore[attr-defined]
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/browser-session/inspect", headers=auth
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "BROWSER_SESSION_NOT_FOUND"


class TestOwnership:
    """A session is scoped to one (user, application) pair. Another user must
    never be able to discover, inspect, fill, or close it."""

    async def test_cross_user_get_is_404(
        self,
        client: object,
        api: str,
        injected_session: tuple[uuid.UUID, uuid.UUID, BrowserSession],
        second_auth: dict[str, str],
    ) -> None:
        _user_id, application_id, _session = injected_session
        response = await client.get(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/browser-session", headers=second_auth
        )
        assert response.status_code == 404

    async def test_cross_user_cannot_close_it(
        self,
        client: object,
        api: str,
        injected_session: tuple[uuid.UUID, uuid.UUID, BrowserSession],
        second_auth: dict[str, str],
    ) -> None:
        user_id, application_id, session = injected_session
        response = await client.delete(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/browser-session", headers=second_auth
        )
        # DELETE is a safe no-op even for a session that is not theirs: it never
        # confirms, and never closes, someone else's session.
        assert response.status_code == 200
        still_open = await session_manager.find_for_application(
            user_id=user_id, application_id=application_id
        )
        assert still_open is not None
        assert still_open.session_id == session.session_id


class TestConflict:
    async def test_a_second_session_for_the_same_application_is_refused(
        self, auth: dict[str, str]
    ) -> None:
        if async_playwright is None:
            pytest.skip(_SKIP_REASON)
        user_id = user_id_from_auth(auth)
        application_id = uuid.uuid4()
        settings = get_settings()
        first = await session_manager.create(
            user_id=user_id,
            application_id=application_id,
            adapter_name="greenhouse",
            headless=True,
            timeout_ms=settings.browser_timeout_ms,
            ttl_seconds=settings.browser_session_ttl_seconds,
        )
        try:
            with pytest.raises(BrowserSessionConflictError):
                await session_manager.create(
                    user_id=user_id,
                    application_id=application_id,
                    adapter_name="greenhouse",
                    headless=True,
                    timeout_ms=settings.browser_timeout_ms,
                    ttl_seconds=settings.browser_session_ttl_seconds,
                )
        finally:
            await session_manager.close(first.session_id)


class TestExpiry:
    async def test_a_stale_session_is_evicted_and_reported_as_expired(
        self, injected_session: tuple[uuid.UUID, uuid.UUID, BrowserSession]
    ) -> None:
        user_id, application_id, session = injected_session
        # No clock patching needed: BrowserSession.last_seen_at is plain data,
        # not read from the system clock at check time until `_is_stale` compares it.
        session.last_seen_at = utcnow().replace(year=2000)
        with pytest.raises(BrowserSessionExpiredError):
            await session_manager.get(
                session.session_id,
                user_id=user_id,
                application_id=application_id,
                ttl_seconds=get_settings().browser_session_ttl_seconds,
            )
        # Expiry closes it: a second lookup finds nothing left to expire.
        assert (
            await session_manager.find_for_application(
                user_id=user_id, application_id=application_id
            )
        ) is None


class TestInspectAndFillAgainstAFixture:
    """Real adapter, real Playwright, real HTTP routes — against a local,
    sanitized fixture rather than a live Greenhouse page.

    ``inspect()`` re-validates the session's origin before reading anything
    from the page (see ``TestInspectRevalidatesOrigin`` below for the
    regression test proving that) — exactly like ``fill()`` already did. A
    session injected directly via ``session_manager.create()`` never
    navigates anywhere real, so its page stays at ``about:blank``, which that
    check correctly refuses. These tests are about adapter/mapping
    correctness, not origin policy (already exhaustively covered in
    ``tests/unit/test_browser_policy.py``), so they bypass it the same way
    ``test_browser_fill_verification.py`` already does for ``fill()``.
    """

    @pytest.fixture(autouse=True)
    def _allow_any_origin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            browser_fill_policy,
            "classify_origin",
            lambda url: OriginDecision(
                url=url, allowed=True, origin="https://job-boards.greenhouse.io"
            ),
        )

    async def test_inspect_proposes_a_mapping_and_flags_the_legal_question(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        seeded_job: object,
        injected_session: tuple[uuid.UUID, uuid.UUID, BrowserSession],
    ) -> None:
        user_id, application_id, session = injected_session
        assert user_id == user_id_from_auth(auth)
        await session.page.set_content(fixture_html("greenhouse_standard.html"))  # type: ignore[attr-defined]

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/browser-session/inspect", headers=auth
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["supported"] is True
        assert body["mapping_token"] is not None

        mappings = {m["field_id"]: m for m in body["mappings"]}
        legal_question = mappings["question_1003"]
        assert legal_question["needs_review"] is True
        assert legal_question["supported_for_fill"] is False
        assert legal_question["proposed_value"] is None
        assert legal_question["sensitive_category"] is None

        combobox = mappings["country"]
        assert combobox["supported_for_fill"] is False

    async def test_get_reflects_the_open_session_after_inspect(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        injected_session: tuple[uuid.UUID, uuid.UUID, BrowserSession],
    ) -> None:
        _user_id, application_id, session = injected_session
        await session.page.set_content(fixture_html("greenhouse_standard.html"))  # type: ignore[attr-defined]
        inspected = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/browser-session/inspect", headers=auth
        )
        assert inspected.status_code == 200, inspected.text
        response = await client.get(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/browser-session", headers=auth
        )
        assert response.status_code == 200
        assert response.json()["session_id"] == session.session_id

    async def test_fill_with_a_stale_mapping_token_is_refused(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        injected_session: tuple[uuid.UUID, uuid.UUID, BrowserSession],
    ) -> None:
        _user_id, application_id, session = injected_session
        await session.page.set_content(fixture_html("greenhouse_standard.html"))  # type: ignore[attr-defined]
        inspected = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/browser-session/inspect", headers=auth
        )
        assert inspected.status_code == 200

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/browser-session/fill",
            headers=auth,
            json={"mapping_token": "0" * 64},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "MAPPING_STALE"

    async def test_an_unsupported_form_is_refused_rather_than_guessed(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        injected_session: tuple[uuid.UUID, uuid.UUID, BrowserSession],
    ) -> None:
        _user_id, application_id, session = injected_session
        await session.page.set_content(fixture_html("unsupported_form.html"))  # type: ignore[attr-defined]
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/browser-session/inspect", headers=auth
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "UNSUPPORTED_FORM"


class TestInspectRevalidatesOrigin:
    """Regression test: ``inspect()`` must re-check the session's current
    origin before reading anything from the page, exactly like ``fill()``
    already did — a page can navigate itself (a client-side redirect, a link
    followed in the visible browser) at any point between session creation
    and an inspect call, and inspecting an unsupported page would otherwise
    build and return a mapping for content CareerPilot never validated.

    This deliberately does *not* use the ``_allow_any_origin`` bypass the
    adapter/mapping tests above rely on — an injected session's page starts
    at ``about:blank`` and is never navigated anywhere, so the *real,
    unpatched* origin check is exactly what this test exercises.
    """

    async def test_inspect_refuses_a_session_not_at_a_supported_origin(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        injected_session: tuple[uuid.UUID, uuid.UUID, BrowserSession],
    ) -> None:
        _user_id, application_id, session = injected_session
        await session.page.set_content(fixture_html("greenhouse_standard.html"))  # type: ignore[attr-defined]
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application_id}/browser-session/inspect", headers=auth
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "UNSUPPORTED_ORIGIN"
