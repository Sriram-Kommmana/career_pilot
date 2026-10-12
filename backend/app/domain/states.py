"""The application state machine.

State lives in the database as a string and is only ever changed through
:func:`assert_transition`, so an illegal move raises instead of quietly corrupting an
application's history.

Two deliberate design points:

* ``SUBMITTING`` is reachable **only** from ``WAITING_FOR_APPROVAL``. There is no other
  edge into it anywhere in this map, which is what makes "nothing is submitted without
  an explicit human approval" a structural property rather than a convention.
* The automation lifecycle (this enum) is kept separate from the real-world result
  (:class:`ApplicationOutcome`). An interview or a rejection is something an employer
  does weeks later; folding that into the agent's state machine would conflate two
  unrelated timelines.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Final

from app.core.errors import InvalidStateTransitionError


class ApplicationState(StrEnum):
    """Where an application is in the discover-to-submit lifecycle."""

    DISCOVERED = "DISCOVERED"
    MATCHED = "MATCHED"
    SELECTED = "SELECTED"
    PREPARING = "PREPARING"
    READY = "READY"
    FILLING = "FILLING"
    VERIFYING = "VERIFYING"
    WAITING_FOR_USER_INPUT = "WAITING_FOR_USER_INPUT"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    # The submission attempt completed but the resulting page did not give evidence
    # either way. Requires a human to reconcile. Never retried automatically.
    SUBMITTED_UNCERTAIN = "SUBMITTED_UNCERTAIN"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ApplicationOutcome(StrEnum):
    """What the employer did. Set by the user, not inferred."""

    NONE = "NONE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    INTERVIEW = "INTERVIEW"
    OFFER = "OFFER"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    CLOSED = "CLOSED"


class InterventionReason(StrEnum):
    """Why the agent stopped and handed control back to the user.

    Each of these is a reason to *pause*, never a problem to engineer around. In
    particular CAPTCHA, MFA and login challenges are resolved by the human in the real
    browser; CareerPilot does not attempt to defeat them.
    """

    NEEDS_LOGIN = "NEEDS_LOGIN"
    NEEDS_CAPTCHA = "NEEDS_CAPTCHA"
    NEEDS_MFA = "NEEDS_MFA"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    JOB_CLOSED = "JOB_CLOSED"
    UNSUPPORTED_FORM = "UNSUPPORTED_FORM"


#: States from which nothing further happens automatically.
TERMINAL_STATES: Final[frozenset[ApplicationState]] = frozenset(
    {
        ApplicationState.SUBMITTED,
        ApplicationState.CANCELLED,
    }
)

#: States where the product is waiting on a person, not on itself.
AWAITING_HUMAN_STATES: Final[frozenset[ApplicationState]] = frozenset(
    {
        ApplicationState.WAITING_FOR_USER_INPUT,
        ApplicationState.WAITING_FOR_APPROVAL,
        ApplicationState.SUBMITTED_UNCERTAIN,
    }
)

_TRANSITIONS: Final[MappingProxyType[ApplicationState, frozenset[ApplicationState]]] = (
    MappingProxyType(
        {
            ApplicationState.DISCOVERED: frozenset(
                {
                    ApplicationState.MATCHED,
                    ApplicationState.SELECTED,
                    ApplicationState.CANCELLED,
                }
            ),
            ApplicationState.MATCHED: frozenset(
                {ApplicationState.SELECTED, ApplicationState.CANCELLED}
            ),
            ApplicationState.SELECTED: frozenset(
                {
                    ApplicationState.PREPARING,
                    ApplicationState.CANCELLED,
                    ApplicationState.FAILED,
                }
            ),
            ApplicationState.PREPARING: frozenset(
                {
                    ApplicationState.READY,
                    ApplicationState.WAITING_FOR_USER_INPUT,
                    ApplicationState.FAILED,
                    ApplicationState.CANCELLED,
                }
            ),
            ApplicationState.READY: frozenset(
                {
                    ApplicationState.FILLING,
                    # Re-preparing is allowed: the job description or profile may have
                    # changed since the answers were drafted.
                    ApplicationState.PREPARING,
                    ApplicationState.FAILED,
                    ApplicationState.CANCELLED,
                }
            ),
            ApplicationState.FILLING: frozenset(
                {
                    ApplicationState.VERIFYING,
                    ApplicationState.WAITING_FOR_USER_INPUT,
                    ApplicationState.FAILED,
                    ApplicationState.CANCELLED,
                }
            ),
            ApplicationState.VERIFYING: frozenset(
                {
                    ApplicationState.WAITING_FOR_APPROVAL,
                    ApplicationState.WAITING_FOR_USER_INPUT,
                    # A failed verification can be retried by filling again.
                    ApplicationState.FILLING,
                    ApplicationState.FAILED,
                    ApplicationState.CANCELLED,
                }
            ),
            ApplicationState.WAITING_FOR_USER_INPUT: frozenset(
                {
                    ApplicationState.PREPARING,
                    ApplicationState.READY,
                    ApplicationState.FILLING,
                    ApplicationState.VERIFYING,
                    ApplicationState.FAILED,
                    ApplicationState.CANCELLED,
                }
            ),
            ApplicationState.WAITING_FOR_APPROVAL: frozenset(
                {
                    # The only edge into SUBMITTING in the entire machine.
                    ApplicationState.SUBMITTING,
                    # The reviewed form changed, so the approval is void and the
                    # application goes back for another pass.
                    ApplicationState.FILLING,
                    ApplicationState.PREPARING,
                    ApplicationState.WAITING_FOR_USER_INPUT,
                    ApplicationState.FAILED,
                    ApplicationState.CANCELLED,
                }
            ),
            ApplicationState.SUBMITTING: frozenset(
                {
                    ApplicationState.SUBMITTED,
                    ApplicationState.SUBMITTED_UNCERTAIN,
                    # e.g. a CAPTCHA appeared on the final step.
                    ApplicationState.WAITING_FOR_USER_INPUT,
                    ApplicationState.FAILED,
                }
            ),
            # Reconciled by a human who checked their inbox or the employer's portal.
            ApplicationState.SUBMITTED_UNCERTAIN: frozenset(
                {
                    ApplicationState.SUBMITTED,
                    ApplicationState.FAILED,
                }
            ),
            ApplicationState.FAILED: frozenset(
                {
                    ApplicationState.PREPARING,
                    ApplicationState.CANCELLED,
                }
            ),
            ApplicationState.SUBMITTED: frozenset(),
            ApplicationState.CANCELLED: frozenset(),
        }
    )
)


def allowed_transitions(state: ApplicationState) -> frozenset[ApplicationState]:
    """Every state reachable in one step from ``state``."""
    return _TRANSITIONS[state]


def can_transition(current: ApplicationState, target: ApplicationState) -> bool:
    return target in _TRANSITIONS[current]


def assert_transition(current: ApplicationState, target: ApplicationState) -> None:
    """Raise unless ``current -> target`` is a legal move.

    Self-transitions are rejected. A no-op write should not produce a history entry
    implying something happened.
    """
    if current == target:
        raise InvalidStateTransitionError(
            f"Application is already in state {current}.",
            details={"current_state": current.value, "target_state": target.value},
        )
    if not can_transition(current, target):
        allowed = sorted(s.value for s in _TRANSITIONS[current])
        raise InvalidStateTransitionError(
            f"Cannot move an application from {current} to {target}.",
            details={
                "current_state": current.value,
                "target_state": target.value,
                "allowed_target_states": allowed,
            },
        )


def shortest_path(
    current: ApplicationState, target: ApplicationState
) -> list[ApplicationState] | None:
    """The shortest legal sequence of states from ``current`` to ``target``.

    Returns the states to move *through*, excluding ``current`` and including
    ``target``, or ``None`` when ``target`` is unreachable. Used where a caller needs
    to reach a state several steps away - recording a submission the user made
    themselves, for instance - without hand-writing the sequence and without
    bypassing the transition map.
    """
    if current == target:
        return []

    from collections import deque

    queue: deque[tuple[ApplicationState, list[ApplicationState]]] = deque([(current, [])])
    seen: set[ApplicationState] = {current}
    while queue:
        state, path = queue.popleft()
        for nxt in sorted(_TRANSITIONS[state], key=lambda s: s.value):
            if nxt in seen:
                continue
            next_path = [*path, nxt]
            if nxt == target:
                return next_path
            seen.add(nxt)
            queue.append((nxt, next_path))
    return None


def is_terminal(state: ApplicationState) -> bool:
    return state in TERMINAL_STATES


def is_awaiting_human(state: ApplicationState) -> bool:
    return state in AWAITING_HUMAN_STATES


#: Tracker column grouping used by the dashboard and the Kanban board. Keeping this
#: beside the state machine means a new state cannot be added without deciding where
#: the user sees it; :func:`tracker_column` raises for anything unmapped.
_TRACKER_COLUMNS: Final[MappingProxyType[ApplicationState, str]] = MappingProxyType(
    {
        ApplicationState.DISCOVERED: "saved",
        ApplicationState.MATCHED: "saved",
        ApplicationState.SELECTED: "saved",
        ApplicationState.PREPARING: "preparing",
        ApplicationState.READY: "preparing",
        ApplicationState.FILLING: "preparing",
        ApplicationState.VERIFYING: "preparing",
        ApplicationState.WAITING_FOR_USER_INPUT: "awaiting_input",
        ApplicationState.WAITING_FOR_APPROVAL: "awaiting_approval",
        ApplicationState.SUBMITTING: "submitting",
        ApplicationState.SUBMITTED: "submitted",
        ApplicationState.SUBMITTED_UNCERTAIN: "needs_reconciliation",
        ApplicationState.FAILED: "failed",
        ApplicationState.CANCELLED: "cancelled",
    }
)

TRACKER_COLUMNS: Final[tuple[str, ...]] = (
    "saved",
    "preparing",
    "awaiting_input",
    "awaiting_approval",
    "submitting",
    "submitted",
    "needs_reconciliation",
    "failed",
    "cancelled",
)


def tracker_column(state: ApplicationState) -> str:
    """Which tracker column a state belongs in."""
    try:
        return _TRACKER_COLUMNS[state]
    except KeyError as exc:  # pragma: no cover - guarded by test_states.py
        raise KeyError(f"State {state} has no tracker column. Add one in domain.states.") from exc
