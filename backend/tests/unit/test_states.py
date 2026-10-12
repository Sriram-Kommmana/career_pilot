"""The application state machine.

The load-bearing property is checked first and explicitly: ``SUBMITTING`` is reachable
only from ``WAITING_FOR_APPROVAL``. That is what makes "nothing is submitted without an
explicit human approval" structural rather than conventional, so it is asserted against
the whole transition map rather than against a hand-picked list of states.
"""

from __future__ import annotations

import pytest

from app.core.errors import InvalidStateTransitionError
from app.domain.states import (
    TERMINAL_STATES,
    TRACKER_COLUMNS,
    ApplicationState,
    allowed_transitions,
    assert_transition,
    can_transition,
    is_awaiting_human,
    is_terminal,
    shortest_path,
    tracker_column,
)

pytestmark = pytest.mark.unit


def test_submitting_is_reachable_only_from_waiting_for_approval() -> None:
    """The central invariant of the whole product.

    Scanned over every state in the enum, so a future edit that adds another edge
    into SUBMITTING fails here instead of quietly widening the submission path.
    """
    sources = [
        state
        for state in ApplicationState
        if ApplicationState.SUBMITTING in allowed_transitions(state)
    ]
    assert sources == [ApplicationState.WAITING_FOR_APPROVAL]


def test_submitted_is_reachable_only_from_submitting_or_reconciliation() -> None:
    sources = {
        state
        for state in ApplicationState
        if ApplicationState.SUBMITTED in allowed_transitions(state)
    }
    assert sources == {
        ApplicationState.SUBMITTING,
        # A human reconciling an ambiguous result.
        ApplicationState.SUBMITTED_UNCERTAIN,
    }


def test_every_state_has_a_transition_entry() -> None:
    """A new state without an entry would raise a KeyError at runtime."""
    for state in ApplicationState:
        assert isinstance(allowed_transitions(state), frozenset)


def test_every_state_has_a_tracker_column() -> None:
    """A new state must be given a place in the UI, not silently disappear."""
    for state in ApplicationState:
        assert tracker_column(state) in TRACKER_COLUMNS


def test_terminal_states_have_no_outgoing_transitions() -> None:
    for state in TERMINAL_STATES:
        assert allowed_transitions(state) == frozenset()


def test_terminal_states_are_submitted_and_cancelled() -> None:
    assert is_terminal(ApplicationState.SUBMITTED)
    assert is_terminal(ApplicationState.CANCELLED)
    # Deliberately not terminal: a human still has to reconcile it.
    assert not is_terminal(ApplicationState.SUBMITTED_UNCERTAIN)
    # Deliberately not terminal: a failed application can be retried.
    assert not is_terminal(ApplicationState.FAILED)


def test_states_awaiting_a_human_are_flagged() -> None:
    assert is_awaiting_human(ApplicationState.WAITING_FOR_USER_INPUT)
    assert is_awaiting_human(ApplicationState.WAITING_FOR_APPROVAL)
    assert is_awaiting_human(ApplicationState.SUBMITTED_UNCERTAIN)
    assert not is_awaiting_human(ApplicationState.FILLING)


def test_a_legal_transition_is_accepted() -> None:
    assert_transition(ApplicationState.SELECTED, ApplicationState.PREPARING)


def test_an_illegal_transition_raises_and_lists_the_alternatives() -> None:
    with pytest.raises(InvalidStateTransitionError) as caught:
        assert_transition(ApplicationState.SELECTED, ApplicationState.SUBMITTED)

    details = caught.value.details
    assert isinstance(details, dict)
    assert details["current_state"] == "SELECTED"
    assert details["target_state"] == "SUBMITTED"
    # The error tells the caller what it *could* have done.
    assert "PREPARING" in details["allowed_target_states"]


def test_self_transition_is_rejected() -> None:
    """A no-op write should not produce history implying something happened."""
    with pytest.raises(InvalidStateTransitionError, match="already in state"):
        assert_transition(ApplicationState.READY, ApplicationState.READY)


def test_cannot_jump_straight_from_selected_to_submitting() -> None:
    assert not can_transition(ApplicationState.SELECTED, ApplicationState.SUBMITTING)


def test_cannot_escape_a_cancelled_application() -> None:
    for state in ApplicationState:
        assert not can_transition(ApplicationState.CANCELLED, state)


def test_failed_can_be_retried_but_not_jumped_to_submitted() -> None:
    assert can_transition(ApplicationState.FAILED, ApplicationState.PREPARING)
    assert not can_transition(ApplicationState.FAILED, ApplicationState.SUBMITTED)


def test_verification_failure_can_lead_back_to_filling() -> None:
    """A failed read-back is retryable, which is why VERIFYING -> FILLING exists."""
    assert can_transition(ApplicationState.VERIFYING, ApplicationState.FILLING)


def test_approval_can_be_sent_back_when_the_form_changes() -> None:
    """Invalidating an approval has to lead somewhere legal."""
    assert can_transition(ApplicationState.WAITING_FOR_APPROVAL, ApplicationState.FILLING)
    assert can_transition(ApplicationState.WAITING_FOR_APPROVAL, ApplicationState.PREPARING)


class TestShortestPath:
    def test_identity_is_an_empty_path(self) -> None:
        assert shortest_path(ApplicationState.READY, ApplicationState.READY) == []

    def test_adjacent_states_give_a_single_step(self) -> None:
        assert shortest_path(ApplicationState.SELECTED, ApplicationState.PREPARING) == [
            ApplicationState.PREPARING
        ]

    def test_every_step_of_a_returned_path_is_legal(self) -> None:
        path = shortest_path(ApplicationState.SELECTED, ApplicationState.SUBMITTED)
        assert path is not None
        current = ApplicationState.SELECTED
        for step in path:
            assert can_transition(current, step), f"{current} -> {step} is not legal"
            current = step
        assert current is ApplicationState.SUBMITTED

    def test_a_path_to_submitted_must_pass_through_approval(self) -> None:
        """Even a shortest path cannot route around the approval state."""
        path = shortest_path(ApplicationState.SELECTED, ApplicationState.SUBMITTED)
        assert path is not None
        assert ApplicationState.WAITING_FOR_APPROVAL in path
        assert ApplicationState.SUBMITTING in path

    def test_unreachable_target_returns_none(self) -> None:
        assert shortest_path(ApplicationState.CANCELLED, ApplicationState.SUBMITTED) is None
