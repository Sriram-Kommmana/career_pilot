"""In-memory browser session management.

The same pattern as ``app.services.events.ActivityBroker``: one process-wide
manager holding live state in a dict, nothing persisted to the database. A
session id is a ``secrets.token_urlsafe(32)`` string — the same unguessable-id
convention already used for ``Application.submission_idempotency_key``.

Deliberately not a database row: the requirement that "a restarted or
disconnected browser session should require reinspection and renewed
approval" is true automatically when the state lives only in process memory.
There is no cleanup job to forget to run and no stale row to reconcile.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from app.browser.adapters.base import FormInspectionResult
from app.browser.mapping import ProposedMapping
from app.core.clock import utcnow
from app.core.errors import (
    BrowserSessionConflictError,
    BrowserSessionExpiredError,
    BrowserSessionNotFoundError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class FieldFillOutcome:
    field_id: str
    action_result: str  # "filled" | "fill_failed" | "skipped"
    verification: str  # VerificationStatus.value
    verification_note: str | None = None


@dataclass(slots=True)
class BrowserSession:
    session_id: str
    user_id: uuid.UUID
    application_id: uuid.UUID
    adapter_name: str
    #: Real Playwright handles. Never serialised, never leave this process.
    playwright: object
    browser: object
    context: object
    page: object
    created_at: datetime
    last_seen_at: datetime
    agent_run_id: uuid.UUID | None = None
    last_inspection: FormInspectionResult | None = None
    last_mapping: tuple[ProposedMapping, ...] = field(default_factory=tuple)
    mapping_token: str | None = None
    last_fill_outcomes: tuple[FieldFillOutcome, ...] = field(default_factory=tuple)
    #: The page's own text/URL captured immediately before the submit click —
    #: never before that, so it reflects the form exactly as approved. Kept
    #: so a later reconciliation of an uncertain outcome, on this same still-
    #: open session, can tell a confirmation that is genuinely new since the
    #: attempt apart from text that was already sitting on the page (and
    #: would otherwise look like corroborating evidence merely because it
    #: happens to still be there).
    submission_baseline_text: str | None = None
    submission_baseline_url: str | None = None


class BrowserSessionManager:
    """Owns every live browser session in this process."""

    def __init__(self) -> None:
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        application_id: uuid.UUID,
        adapter_name: str,
        headless: bool,
        timeout_ms: int,
        ttl_seconds: int,
    ) -> BrowserSession:
        from playwright.async_api import async_playwright

        async with self._lock:
            await self._evict_expired_locked(ttl_seconds=ttl_seconds)
            for existing in self._sessions.values():
                if existing.application_id == application_id:
                    raise BrowserSessionConflictError(
                        "A browser session for this application is already open. "
                        "Close it before starting a new one.",
                        details={"session_id": existing.session_id},
                    )

            playwright = await async_playwright().start()
            try:
                browser = await playwright.chromium.launch(headless=headless)
                context = await browser.new_context()
                page = await context.new_page()
                page.set_default_timeout(timeout_ms)
            except Exception:
                await playwright.stop()
                raise

            session_id = secrets.token_urlsafe(32)
            now = utcnow()
            session = BrowserSession(
                session_id=session_id,
                user_id=user_id,
                application_id=application_id,
                adapter_name=adapter_name,
                playwright=playwright,
                browser=browser,
                context=context,
                page=page,
                created_at=now,
                last_seen_at=now,
            )
            self._sessions[session_id] = session
            logger.info(
                "browser_session_created",
                session_id=session_id,
                user_id=str(user_id),
                application_id=str(application_id),
            )
            return session

    async def get(
        self,
        session_id: str,
        *,
        user_id: uuid.UUID,
        application_id: uuid.UUID,
        ttl_seconds: int,
    ) -> BrowserSession:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise BrowserSessionNotFoundError("No browser session with this id.")
            if session.user_id != user_id or session.application_id != application_id:
                # Same response as a genuinely missing id — never confirms that
                # another user's session exists.
                raise BrowserSessionNotFoundError("No browser session with this id.")

            if self._is_stale(session, ttl_seconds=ttl_seconds):
                await self._close_locked(session_id)
                raise BrowserSessionExpiredError(
                    "This browser session has expired. Start a new one and "
                    "re-inspect the form."
                )

            page = session.page
            if hasattr(page, "is_closed") and page.is_closed():
                await self._close_locked(session_id)
                raise BrowserSessionExpiredError(
                    "The browser disconnected. Start a new session and re-inspect."
                )

            session.last_seen_at = utcnow()
            return session

    async def find_for_application(
        self, *, user_id: uuid.UUID, application_id: uuid.UUID
    ) -> BrowserSession | None:
        async with self._lock:
            for session in self._sessions.values():
                if session.user_id == user_id and session.application_id == application_id:
                    return session
            return None

    async def close(self, session_id: str) -> None:
        async with self._lock:
            await self._close_locked(session_id)

    async def _close_locked(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        try:
            await session.context.close()  # type: ignore[attr-defined]
            await session.browser.close()  # type: ignore[attr-defined]
            await session.playwright.stop()  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - best-effort cleanup
            logger.warning("browser_session_close_failed", session_id=session_id, error=str(exc))
        logger.info("browser_session_closed", session_id=session_id)

    def _is_stale(self, session: BrowserSession, *, ttl_seconds: int) -> bool:
        age = (utcnow() - session.last_seen_at).total_seconds()
        return age > ttl_seconds

    async def _evict_expired_locked(self, *, ttl_seconds: int) -> None:
        """Opportunistic cleanup, run while already holding the lock during
        ``create()``. Not a background job — there is none in this project by
        design (no Redis/worker infrastructure). Awaited directly, under the
        same lock, rather than fired-and-forgotten, so there is never a window
        where a session is being closed without the lock held."""
        expired = [
            sid for sid, s in self._sessions.items() if self._is_stale(s, ttl_seconds=ttl_seconds)
        ]
        for sid in expired:
            await self._close_locked(sid)


#: Process-wide manager, exactly like `app.services.events.broker`.
manager = BrowserSessionManager()
