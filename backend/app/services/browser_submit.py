"""Phase 6: the actual submission click, and nothing before it is trusted blindly.

Everything safety-critical is reused, never reimplemented: the submission gate
(``app.domain.submission_gate``), the state machine, the approval/form-hash
mechanism, and ``applications.claim_submission_attempt`` /
``applications.record_submission_outcome`` for the DB side. This module's own
job is narrow — it is the one place that is allowed to drive a real browser
click — and it is built around one hard constraint that does not exist
anywhere else in this codebase: the action it performs cannot be rolled back.

That constraint shapes everything below:

* Every check that can still be safely refused (ownership, origin, a session
  that has actually been inspected, live-page drift, a deterministic submit
  control) happens *before* any durable write, using read-only queries or a
  committed-and-then-raised invalidation when the live page no longer matches
  what was approved.
* The one point of no return — claiming the idempotency key and moving to
  ``SUBMITTING`` — is committed in its own transaction (:func:`session_scope`,
  not the caller's request-scoped session) *before* the click, so a crash
  between the click and recording its outcome leaves durable evidence that an
  attempt may have reached the employer, never a silently rolled-back nothing.
* Every possible outcome of the click — confirmed success, confirmed failure,
  or uncertain — is recorded in its own final, committed transaction, and
  every one of them is terminal for that attempt. None is retried
  automatically.
* A per-application in-process lock serialises concurrent calls (a double
  click, two tabs, a retried request) so two submissions for the same
  application can never both pass the "not already submitting" check before
  either commits. ``with_for_update=True`` on the claim step's row read is a
  second, database-level layer of the same protection.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from urllib.parse import urlsplit

from app.browser import policy
from app.browser.adapters.base import FormAdapter, SubmitAttempt
from app.browser.adapters.greenhouse import (
    SUCCESS_TEXT_MARKERS,
    GreenhouseAdapter,
    _first_new_marker,
)
from app.browser.session import BrowserSession
from app.browser.session import manager as session_manager
from app.core.config import Settings, get_settings
from app.core.errors import (
    ConflictError,
    SessionNotInspectedError,
    SubmitControlNotFoundError,
    UnsupportedOriginError,
)
from app.core.logging import get_logger
from app.db.session import session_scope
from app.domain.approval import FieldResolution, VerificationStatus
from app.domain.states import ApplicationState
from app.models import Application
from app.services import applications as applications_service

logger = get_logger(__name__)

_ADAPTERS: dict[str, FormAdapter] = {"greenhouse": GreenhouseAdapter()}

#: One lock per application id, so concurrent submit calls for the *same*
#: application queue up instead of both reaching the browser at once. Mirrors
#: ``app.browser.session.BrowserSessionManager``'s own lock discipline.
_locks: dict[uuid.UUID, asyncio.Lock] = defaultdict(asyncio.Lock)


def _lock_for(application_id: uuid.UUID) -> asyncio.Lock:
    return _locks[application_id]


async def _resolve_owned_session(
    *, user_id: uuid.UUID, application_id: uuid.UUID, settings: Settings
) -> BrowserSession:
    existing = await session_manager.find_for_application(
        user_id=user_id, application_id=application_id
    )
    if existing is None:
        from app.core.errors import BrowserSessionNotFoundError

        raise BrowserSessionNotFoundError(
            "No browser session is open for this application. Open one, inspect "
            "the live page, and fill it before submitting."
        )
    return await session_manager.get(
        existing.session_id,
        user_id=user_id,
        application_id=application_id,
        ttl_seconds=settings.browser_session_ttl_seconds,
    )


def _same_application_page(live_url: str, expected_url: str) -> bool:
    live = urlsplit(live_url)
    expected = urlsplit(expected_url)
    return (
        live.scheme,
        live.netloc,
        live.path.rstrip("/"),
    ) == (expected.scheme, expected.netloc, expected.path.rstrip("/"))


async def _revalidate_live_fields_or_raise(
    *,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    browser_session: BrowserSession,
) -> None:
    """Re-reads every field this session filled and verified from the *live*
    page right now, before trusting the stored verification.

    Any drift — the field's live value no longer matches what we actually
    filled it with, the field disappeared from the page, or the browser is
    simply on a different page than the one this application's approval
    covers — is persisted as an invalidation (the affected field's
    ``verification`` is set back to ``FAILED``, which changes the form hash
    and revokes the stale approval via the existing ``_recompute_form_state``)
    in its own committed transaction, so the invalidation survives even
    though this function then raises a refusal. The existing gate re-check
    downstream is what actually stops anything from being submitted; this is
    what makes sure it is checking against reality, not against however the
    page looked several minutes ago.

    The comparison is against ``mapping.proposed_value`` — the exact value
    this session filled the field with, mirroring ``browser_fill.fill``'s own
    verification check exactly — never against the confirmed-data row's
    stored value directly: a split field (``first_name``/``last_name`` both
    sourced from one ``full_name`` row) would otherwise never match its own
    row's full value and look drifted when nothing changed. Fields the user
    typed directly into the visible browser (no CareerPilot-proposed value to
    compare against) are outside this check's scope — the gate's own
    freshness window is what bounds how long an unchecked value can stand.
    """
    adapter = _ADAPTERS[browser_session.adapter_name]
    fresh = await adapter.inspect(browser_session.page)
    drifted_labels: list[str] = []

    async with session_scope() as db:
        application = await applications_service.get_application(
            db, user_id=user_id, application_id=application_id
        )
        if not _same_application_page(
            browser_session.page.url,  # type: ignore[attr-defined]
            application.application_url,
        ):
            raise UnsupportedOriginError(
                "The browser has navigated away from this application's page. "
                "Re-open a session on the correct page before submitting."
            )
        if not fresh.supported:
            from app.core.errors import UnsupportedFormError

            raise UnsupportedFormError(
                fresh.reason or "This application form is no longer recognised."
            )

        fresh_by_id = {f.field_id: f for f in fresh.fields}
        fields_by_key = {f.field_key: f for f in application.fields}

        for mapping in browser_session.last_mapping:
            if (
                mapping.source_kind != "application_field"
                or mapping.proposed_value is None
                or not mapping.supported_for_fill
                or mapping.needs_review
            ):
                continue
            row = fields_by_key.get(mapping.source_id or "")
            if (
                row is None
                or row.resolution is not FieldResolution.RESOLVED
                or row.verification is not VerificationStatus.PASSED
            ):
                continue

            extracted = fresh_by_id.get(mapping.field_id)
            matches = False
            if extracted is not None:
                try:
                    read_back = await adapter.read_field(browser_session.page, extracted)
                except Exception:
                    matches = False
                else:
                    matches = (
                        read_back.matches_intended
                        if extracted.field_kind == "file"
                        else read_back.value == mapping.proposed_value
                    )
            if not matches:
                row.verification = VerificationStatus.FAILED
                row.verification_note = (
                    "The live page no longer reflects this previously verified value."
                )
                drifted_labels.append(row.label or row.field_key)

        if drifted_labels:
            await applications_service._recompute_form_state(
                db, application=application, user_id=user_id
            )
        # The block exits normally either way, so a real invalidation above
        # is actually committed — raising from *inside* this `async with`
        # would instead roll it back (session_scope rolls back on exception),
        # which would silently undo the one write this function exists to
        # guarantee survives a refusal.

    if drifted_labels:
        raise ConflictError(
            "The live application page no longer matches what you approved. "
            f"{len(drifted_labels)} field(s) changed: review and approve the "
            "current version again before submitting.",
            code="APPROVAL_STALE",
            details={"changed_fields": drifted_labels},
        )


async def submit(
    *,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    settings: Settings | None = None,
) -> Application:
    """Submit an approved, verified application through its real browser session.

    Raises a clean refusal (nothing durable written) for every check up to and
    including locating the submit control. Past that point, the attempt is
    claimed durably before the click, and every possible outcome is recorded —
    never left ambiguous in the database even if it is ambiguous on the page.
    """
    cfg = settings or get_settings()

    async with _lock_for(application_id):
        browser_session = await _resolve_owned_session(
            user_id=user_id, application_id=application_id, settings=cfg
        )

        landed = policy.classify_origin(browser_session.page.url)  # type: ignore[attr-defined]
        if not landed.allowed:
            raise UnsupportedOriginError(landed.explain())

        if browser_session.last_inspection is None or not browser_session.last_mapping:
            raise SessionNotInspectedError(
                "This browser session has not been inspected yet. Inspect and "
                "fill the form on this session before submitting — an approval "
                "is bound to a session that actually reviewed the page, not "
                "merely one that is open."
            )

        await _revalidate_live_fields_or_raise(
            user_id=user_id, application_id=application_id, browser_session=browser_session
        )

        adapter = _ADAPTERS[browser_session.adapter_name]
        control = await adapter.find_submit_control(browser_session.page)
        if control is None:
            raise SubmitControlNotFoundError(
                "No single, unambiguous submit control was found on this page. "
                "Finish submitting this application by hand in the visible browser."
            )

        # Captured now — immediately before the point of no return, the
        # closest this can get to "exactly before the click" — and kept on
        # the session so a later reconciliation of an uncertain outcome (on
        # this same still-open session) can tell a genuinely new
        # confirmation apart from text that was already on the page.
        try:
            browser_session.submission_baseline_text = (
                await browser_session.page.inner_text("body")  # type: ignore[attr-defined]
            ).lower()
        except Exception:
            browser_session.submission_baseline_text = ""
        browser_session.submission_baseline_url = browser_session.page.url  # type: ignore[attr-defined]

        # Point of no return: claim the attempt durably, in its own transaction,
        # before touching the real control.
        async with session_scope() as db:
            application = await applications_service.claim_submission_attempt(
                db, user_id=user_id, application_id=application_id, settings=cfg
            )
            agent_run_id = browser_session.agent_run_id

        logger.info(
            "browser_submission_attempt_claimed",
            application_id=str(application_id),
            session_id=browser_session.session_id,
        )

        try:
            outcome: SubmitAttempt = await adapter.submit(
                browser_session.page,
                control,
                confirmation_timeout_ms=cfg.browser_submission_confirmation_timeout_ms,
            )
        except Exception as exc:
            logger.warning(
                "browser_submit_unexpected_error",
                application_id=str(application_id),
                error_type=type(exc).__name__,
            )
            outcome = SubmitAttempt(
                outcome="UNCERTAIN",
                evidence=(
                    "An unexpected error occurred while submitting; the outcome "
                    "could not be confirmed."
                ),
            )

        async with session_scope() as db:
            application = await applications_service.record_submission_outcome(
                db,
                user_id=user_id,
                application_id=application_id,
                outcome=outcome.outcome,
                evidence=outcome.evidence,
            )
            if agent_run_id is not None:
                from app.models import ToolExecution

                db.add(
                    ToolExecution(
                        agent_run_id=agent_run_id,
                        sequence=20,
                        tool_name="browser.submit",
                        status="succeeded" if outcome.outcome != "UNCERTAIN" else "uncertain",
                        arguments_redacted={},
                        result_redacted={"outcome": outcome.outcome},
                    )
                )

        logger.info(
            "browser_submission_outcome_recorded",
            application_id=str(application_id),
            outcome=outcome.outcome,
        )
        return application


async def reconcile(
    *,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    outcome: ApplicationState,
    note: str | None,
) -> Application:
    """Resolve a ``SUBMITTED_UNCERTAIN`` application. Never clicks anything.

    If a browser session for this application happens to still be open, this
    passively re-reads the live page for the same confirmation markers
    ``submit()`` looks for (read-only — no click, no fill, no navigation) and
    records a match as corroborating evidence only if it is trustworthy by
    the same standard ``submit()`` itself applies: either it is genuinely
    new since the baseline captured immediately before the original click
    (when this is the same still-open session that made the attempt), or —
    if no such baseline is available (a different or reopened session) — the
    application form is no longer present, since a marker that is merely
    sitting somewhere on an unchanged page proves nothing about what
    happened. A marker that fails both checks is not corroborating evidence,
    and this never resubmits anything to find out. The final ``outcome`` is
    always the caller's explicit attestation, never silently overridden or
    inferred from this check.
    """
    cfg = get_settings()
    checked_live_page = False

    existing = await session_manager.find_for_application(
        user_id=user_id, application_id=application_id
    )
    if existing is not None:
        try:
            browser_session = await session_manager.get(
                existing.session_id,
                user_id=user_id,
                application_id=application_id,
                ttl_seconds=cfg.browser_session_ttl_seconds,
            )
            adapter = _ADAPTERS[browser_session.adapter_name]
            text, _ = await adapter._snapshot(browser_session.page)  # type: ignore[attr-defined]
            baseline = browser_session.submission_baseline_text
            if baseline is not None:
                marker = _first_new_marker(text, SUCCESS_TEXT_MARKERS, baseline)
                checked_live_page = marker is not None
            else:
                marker = next((m for m in SUCCESS_TEXT_MARKERS if m in text), None)
                checked_live_page = marker is not None and not await adapter._form_is_present(  # type: ignore[attr-defined]
                    browser_session.page
                )
        except Exception as exc:
            logger.debug(
                "browser_reconcile_passive_check_failed",
                application_id=str(application_id),
                error_type=type(exc).__name__,
            )

    async with session_scope() as db:
        return await applications_service.reconcile_submission(
            db,
            user_id=user_id,
            application_id=application_id,
            outcome=outcome,
            note=note,
            checked_live_page=checked_live_page,
        )
