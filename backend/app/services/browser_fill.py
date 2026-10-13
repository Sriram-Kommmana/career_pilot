"""Orchestrates Phase 5's browser session over existing services.

Deliberately thin: everything safety-critical is either reused unchanged
(``applications.transition``, ``applications._recompute_form_state``, the
submission gate) or lives in the pure ``app.browser.mapping``/``app.browser.policy``
modules this file calls. This module's own job is narrow: validate a request
against the live session, drive the adapter, and write the result onto the
*existing* ``ApplicationField``/``Application`` columns those other pieces
already read.

Approval A (filling) is intentionally **not** the submission gate: a
``mapping_token`` (hashed the same way ``compute_form_state_hash`` hashes a
form, but over the proposed mapping) binds the fill request to the exact
mapping the user reviewed. A stale token is refused by this module, never by
``app.domain.submission_gate``, and never resembles an ``ApprovalRecordRow``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.browser import mapping as mapping_module
from app.browser import policy
from app.browser.adapters.base import ExtractedField, FormAdapter
from app.browser.adapters.greenhouse import GreenhouseAdapter
from app.browser.session import BrowserSession, FieldFillOutcome
from app.browser.session import manager as session_manager
from app.core.clock import utcnow
from app.core.config import Settings, get_settings
from app.core.errors import (
    ConflictError,
    MappingStaleError,
    UnsupportedFormError,
    UnsupportedOriginError,
)
from app.core.logging import get_logger
from app.domain.approval import VerificationStatus
from app.domain.provenance import ProvenanceKind
from app.domain.states import ApplicationState, InterventionReason
from app.models import AgentRun, Application, ApplicationField, ToolExecution
from app.services import applications as applications_service

logger = get_logger(__name__)

_ADAPTERS: dict[str, FormAdapter] = {"greenhouse": GreenhouseAdapter()}


@dataclass(frozen=True, slots=True)
class InspectionOutcome:
    session_id: str
    origin: str
    supported: bool
    reason: str | None
    extracted_fields: tuple[ExtractedField, ...]
    proposed_mappings: tuple[mapping_module.ProposedMapping, ...]
    mapping_token: str | None


@dataclass(frozen=True, slots=True)
class FillValidationReport:
    session_id: str
    outcomes: tuple[FieldFillOutcome, ...]
    filled_count: int
    failed_count: int
    still_needs_human: tuple[str, ...]
    application_state: str
    verification_status: str


def _mapping_token(mappings: tuple[mapping_module.ProposedMapping, ...]) -> str:
    payload = sorted(
        (
            {
                "field_id": m.field_id,
                "proposed_value": m.proposed_value,
                "needs_review": m.needs_review,
                "supported_for_fill": m.supported_for_fill,
            }
            for m in mappings
        ),
        key=lambda item: str(item["field_id"]),
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _record_tool_execution(
    session: AsyncSession,
    *,
    agent_run_id: uuid.UUID,
    sequence: int,
    tool_name: str,
    status: str,
    arguments: dict[str, object],
    result: dict[str, object] | None = None,
    error: str | None = None,
) -> None:
    """Mirrors ``discovery._record_tool_execution`` exactly: redacted
    arguments/results, never a raw field value or a cookie."""
    session.add(
        ToolExecution(
            agent_run_id=agent_run_id,
            sequence=sequence,
            tool_name=tool_name,
            status=status,
            arguments_redacted=arguments,
            result_redacted=result or {},
            error=error,
        )
    )
    await session.flush()


async def start_session(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    settings: Settings | None = None,
) -> BrowserSession:
    """Create a browser session for an owned application and navigate to it.

    Explicit, never implicit: this is the only code path that opens a browser,
    and it only runs when a user asks for it.
    """
    cfg = settings or get_settings()
    application = await applications_service.get_application(
        db, user_id=user_id, application_id=application_id
    )

    decision = policy.classify_origin(application.application_url)
    if not decision.allowed:
        raise UnsupportedOriginError(
            decision.explain(), details={"url": application.application_url}
        )

    browser_session = await session_manager.create(
        user_id=user_id,
        application_id=application_id,
        adapter_name="greenhouse",
        headless=cfg.browser_headless,
        timeout_ms=cfg.browser_timeout_ms,
        ttl_seconds=cfg.browser_session_ttl_seconds,
    )

    agent_run = AgentRun(
        user_id=user_id,
        application_id=application_id,
        kind="browser_fill",
        status="running",
        correlation_id=browser_session.session_id,
        context={"origin": decision.origin},
    )
    db.add(agent_run)
    await db.flush()
    browser_session.agent_run_id = agent_run.id

    try:
        page = browser_session.page
        await page.goto(application.application_url)  # type: ignore[attr-defined]

        # A page can redirect anywhere during navigation. Re-check the origin
        # it actually landed on — never trust the URL we asked for.
        landed_decision = policy.classify_origin(page.url)  # type: ignore[attr-defined]
        if not landed_decision.allowed:
            await session_manager.close(browser_session.session_id)
            raise UnsupportedOriginError(
                f"The page redirected to an unsupported origin: {landed_decision.explain()}",
                details={"landed_url": page.url},  # type: ignore[attr-defined]
            )
    except UnsupportedOriginError:
        raise
    except Exception as exc:
        await session_manager.close(browser_session.session_id)
        raise ConflictError(f"Could not open the application page: {exc}") from exc

    await _record_tool_execution(
        db,
        agent_run_id=agent_run.id,
        sequence=1,
        tool_name="browser.open",
        status="succeeded",
        arguments={"origin": decision.origin},
    )
    return browser_session


def _confirmed_field_sources(
    application: Application,
) -> tuple[mapping_module.ConfirmedFieldSource, ...]:
    return tuple(
        mapping_module.ConfirmedFieldSource(
            field_key=f.field_key,
            label=f.label,
            value=f.value_masked,
            provenance=f.value_provenance.value,
            resolution=f.resolution.value,
            needs_human=f.needs_human,
            sensitive_category=f.sensitive_category.value if f.sensitive_category else None,
        )
        for f in application.fields
    )


def _confirmed_answer_sources(
    application: Application,
) -> tuple[mapping_module.ConfirmedAnswerSource, ...]:
    return tuple(
        mapping_module.ConfirmedAnswerSource(
            question_key=a.question_key,
            text=a.answer_text,
            provenance=a.provenance.value,
            is_generated=a.is_generated,
            needs_human=a.needs_human,
        )
        for a in application.answers
    )


async def inspect(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    session_id: str,
    settings: Settings | None = None,
) -> InspectionOutcome:
    """Inspect (or re-inspect) the live form and propose a mapping."""
    cfg = settings or get_settings()
    browser_session = await session_manager.get(
        session_id,
        user_id=user_id,
        application_id=application_id,
        ttl_seconds=cfg.browser_session_ttl_seconds,
    )

    # The page can navigate itself (a client-side redirect, a link the user
    # followed in the visible browser) at any point between session creation
    # and this call. Re-check before reading anything from it — inspecting an
    # unsupported page and proposing a mapping for it would show the user a
    # review screen for a page CareerPilot never actually validated.
    landed_decision = policy.classify_origin(browser_session.page.url)  # type: ignore[attr-defined]
    if not landed_decision.allowed:
        raise UnsupportedOriginError(landed_decision.explain())

    application = await applications_service.get_application(
        db, user_id=user_id, application_id=application_id
    )

    adapter = _ADAPTERS[browser_session.adapter_name]
    result = await adapter.inspect(browser_session.page)

    sequence = 2
    if browser_session.agent_run_id is not None:
        await _record_tool_execution(
            db,
            agent_run_id=browser_session.agent_run_id,
            sequence=sequence,
            tool_name="browser.inspect",
            status="succeeded" if result.supported else "failed",
            arguments={"origin": result.origin},
            result={"field_count": len(result.fields), "supported": result.supported},
            error=result.reason if not result.supported else None,
        )

    if not result.supported:
        browser_session.last_inspection = result
        browser_session.last_mapping = ()
        browser_session.mapping_token = None
        raise UnsupportedFormError(
            result.reason or "This application form is not supported.",
        )

    full_name_field = next((f for f in application.fields if f.field_key == "full_name"), None)
    mappings = mapping_module.build_proposed_mappings(
        extracted_fields=result.fields,
        confirmed_fields=_confirmed_field_sources(application),
        confirmed_answers=_confirmed_answer_sources(application),
        full_name=full_name_field.value_masked if full_name_field else None,
    )
    token = _mapping_token(mappings)

    browser_session.last_inspection = result
    browser_session.last_mapping = mappings
    browser_session.mapping_token = token

    return InspectionOutcome(
        session_id=session_id,
        origin=result.origin,
        supported=True,
        reason=None,
        extracted_fields=result.fields,
        proposed_mappings=mappings,
        mapping_token=token,
    )


async def fill(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    session_id: str,
    mapping_token: str,
    excluded_field_ids: frozenset[str] = frozenset(),
    settings: Settings | None = None,
) -> FillValidationReport:
    """Fill the approved fields, verify every one by reading the page back,
    and drive the existing state machine/gate with the real result.

    Never fills a field outside the approved mapping, never clicks anything,
    never submits. See ``app.browser.adapters.greenhouse``'s own standing test
    for the enforcement of that at the adapter layer.
    """
    cfg = settings or get_settings()
    browser_session = await session_manager.get(
        session_id,
        user_id=user_id,
        application_id=application_id,
        ttl_seconds=cfg.browser_session_ttl_seconds,
    )
    if browser_session.mapping_token != mapping_token or not browser_session.last_mapping:
        raise MappingStaleError(
            "The approved mapping no longer matches this session's current "
            "proposal. Re-inspect the form and review it again."
        )

    # Re-validate the origin immediately before filling — a session can sit
    # idle between approval and this call.
    landed_decision = policy.classify_origin(browser_session.page.url)  # type: ignore[attr-defined]
    if not landed_decision.allowed:
        raise UnsupportedOriginError(landed_decision.explain())

    application = await applications_service.get_application(
        db, user_id=user_id, application_id=application_id
    )
    if application.state is not ApplicationState.READY:
        raise ConflictError(
            f"An application in {application.state.value} cannot be filled; "
            "it must be READY.",
            code="INVALID_STATE_TRANSITION",
            details={"current_state": application.state.value},
        )

    await applications_service.transition(
        db,
        application=application,
        to_state=ApplicationState.FILLING,
        message="The browser agent is filling the approved fields.",
        actor_user_id=user_id,
    )

    adapter = _ADAPTERS[browser_session.adapter_name]
    fields_by_id = {f.field_id: f for f in browser_session.last_inspection.fields}  # type: ignore[union-attr]
    mappings_by_id = {m.field_id: m for m in browser_session.last_mapping}

    outcomes: list[FieldFillOutcome] = []
    sequence = 10

    for proposed in browser_session.last_mapping:
        if proposed.field_id in excluded_field_ids:
            continue
        if (
            proposed.needs_review
            or not proposed.supported_for_fill
            or proposed.proposed_value is None
        ):
            continue

        extracted = fields_by_id[proposed.field_id]
        try:
            await adapter.fill_field(browser_session.page, extracted, proposed.proposed_value)
            read_back = await adapter.read_field(browser_session.page, extracted)
            verified = read_back.matches_intended and (
                read_back.value == proposed.proposed_value or extracted.field_kind == "file"
            )
            outcome = FieldFillOutcome(
                field_id=proposed.field_id,
                action_result="filled",
                verification=(
                    VerificationStatus.PASSED.value if verified else VerificationStatus.FAILED.value
                ),
                verification_note=(
                    None if verified else "The page did not reflect the intended value."
                ),
            )
        except Exception as exc:
            # Never persist or return the raw exception text: Playwright's own
            # retry/call-log embeds the literal attempted value once a locator
            # resolves (e.g. "fill(\"<value>\")" while retrying against a
            # disabled field) — a real path for a sensitive confirmed value
            # (phone, salary, work authorisation) to leak into a stored,
            # unmasked column. Only the exception's type is safe to keep.
            logger.debug(
                "browser_field_fill_failed",
                field_id=proposed.field_id,
                error_type=type(exc).__name__,
            )
            outcome = FieldFillOutcome(
                field_id=proposed.field_id,
                action_result="fill_failed",
                verification=VerificationStatus.FAILED.value,
                verification_note="Could not fill or verify this field.",
            )
        outcomes.append(outcome)
        sequence += 1

    # Second pass: read back EVERY extracted field, including ones never
    # proposed a value (e.g. a required legal question). A non-empty value
    # there means the user answered it themselves, directly in the visible
    # browser — never invented, only observed.
    for extracted in browser_session.last_inspection.fields:  # type: ignore[union-attr]
        if any(o.field_id == extracted.field_id for o in outcomes):
            continue
        if extracted.unrecognised:
            continue
        try:
            read_back = await adapter.read_field(browser_session.page, extracted)
        except Exception as exc:
            # Same reasoning as the fill-failure branch above: only the
            # exception type is logged, never its message.
            logger.debug(
                "browser_field_readback_failed",
                field_id=extracted.field_id,
                error_type=type(exc).__name__,
            )
            continue
        # A file field's read-back value is always a non-empty descriptive
        # string ("0 file(s)") even when nothing was attached — `value`
        # truthiness alone would misreport an untouched upload as filled.
        # `matches_intended` is what actually says a file is present.
        user_filled = (
            read_back.matches_intended if extracted.field_kind == "file" else bool(read_back.value)
        )
        if user_filled:
            outcomes.append(
                FieldFillOutcome(
                    field_id=extracted.field_id,
                    action_result="user_filled",
                    verification=VerificationStatus.PASSED.value,
                )
            )

    browser_session.last_fill_outcomes = tuple(outcomes)

    if browser_session.agent_run_id is not None:
        await _record_tool_execution(
            db,
            agent_run_id=browser_session.agent_run_id,
            sequence=sequence,
            tool_name="browser.fill_and_verify",
            status="succeeded",
            arguments={"field_count": len(outcomes)},
            result={
                "filled": sum(1 for o in outcomes if o.action_result == "filled"),
                "failed": sum(1 for o in outcomes if o.action_result == "fill_failed"),
                "user_filled": sum(1 for o in outcomes if o.action_result == "user_filled"),
            },
        )

    await _apply_outcomes_to_fields(
        db, application=application, outcomes=outcomes, mappings_by_id=mappings_by_id
    )

    await applications_service.transition(
        db,
        application=application,
        to_state=ApplicationState.VERIFYING,
        message="Checking the filled form against the live page.",
        actor_user_id=user_id,
    )

    await db.refresh(application, ["fields", "answers"])
    required_blocking = [
        f for f in application.fields if f.is_required and f.resolution.value != "SKIPPED"
    ]
    all_required_passed = all(
        f.resolution.value == "RESOLVED" and f.verification.value == "PASSED"
        for f in required_blocking
    )
    still_needs_human = tuple(
        (f.label or f.field_key) for f in application.fields if f.needs_human
    )

    # Recompute the hash *before* recording the real verification result.
    # `_recompute_form_state` treats any form-state-hash change as "the
    # reviewed form no longer exists" and resets `verification_status` to
    # NOT_ATTEMPTED — correct when a user edits an already-approved form, but
    # wrong here: filling the form is *why* the hash just changed, and that
    # must never immediately erase the verification this fill just produced.
    # Settling the hash first, then writing the real result after, means the
    # real result is the one that survives.
    await applications_service._recompute_form_state(
        db, application=application, user_id=user_id
    )

    application.verification_status = (
        VerificationStatus.PASSED if all_required_passed else VerificationStatus.FAILED
    )
    application.verified_at = utcnow()
    await db.flush()

    if all_required_passed and not still_needs_human:
        application.unresolved_interventions = []
        await applications_service.transition(
            db,
            application=application,
            to_state=ApplicationState.WAITING_FOR_APPROVAL,
            message="The form is filled and verified. Review it before approving.",
            actor_user_id=user_id,
        )
    else:
        application.unresolved_interventions = [InterventionReason.NEEDS_HUMAN.value]
        await applications_service.transition(
            db,
            application=application,
            to_state=ApplicationState.WAITING_FOR_USER_INPUT,
            message=(
                f"{len(still_needs_human)} question(s) still need your attention "
                "before this can be reviewed for approval."
            ),
            actor_user_id=user_id,
            payload={"needs_human": list(still_needs_human[:20])},
        )

    await db.refresh(application, ["fields", "answers"])
    return FillValidationReport(
        session_id=session_id,
        outcomes=tuple(outcomes),
        filled_count=sum(1 for o in outcomes if o.action_result in ("filled", "user_filled")),
        failed_count=sum(1 for o in outcomes if o.action_result == "fill_failed"),
        still_needs_human=still_needs_human,
        application_state=application.state.value,
        verification_status=application.verification_status.value,
    )


async def _apply_outcomes_to_fields(
    db: AsyncSession,
    *,
    application: Application,
    outcomes: list[FieldFillOutcome],
    mappings_by_id: dict[str, mapping_module.ProposedMapping],
) -> None:
    """Writes real browser results onto the existing ``ApplicationField`` rows.

    Never touches ``value_provenance`` for a field this code did not itself
    fill from confirmed data — a value the user typed directly into the page
    is recorded as ``USER_DIRECT_INPUT``, and a value CareerPilot proposed
    keeps whatever provenance it already had (never silently promoted to
    "confirmed" merely because the browser could read it back).
    """
    fields_by_key = {f.field_key: f for f in application.fields}

    for outcome in outcomes:
        proposed = mappings_by_id.get(outcome.field_id)
        field_key = (
            proposed.source_id
            if proposed and proposed.source_kind == "application_field"
            else None
        )
        row: ApplicationField | None = fields_by_key.get(field_key) if field_key else None
        if row is None:
            continue

        row.selector = outcome.field_id
        row.verification_note = outcome.verification_note
        row.verified_at = utcnow()
        if outcome.action_result == "user_filled":
            row.value_provenance = ProvenanceKind.USER_DIRECT_INPUT
            row.action_result = "user_filled"
            row.resolution = row.resolution.__class__.RESOLVED
            row.needs_human = False
        elif outcome.action_result == "filled":
            row.action_result = "filled"
        else:
            row.action_result = "fill_failed"
        row.verification = row.verification.__class__(outcome.verification)

    await db.flush()
