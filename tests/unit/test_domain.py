from __future__ import annotations

import pytest

from prat.domain import (
    CARD_TRANSITIONS,
    STEP_ORDER,
    Autonomy,
    CardStatus,
    InvalidTransition,
    RunStatus,
    StepName,
    StepStatus,
    can_transition,
    check_transition,
    is_terminal_card,
    is_terminal_run,
    lowest_autonomy,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (CardStatus.DRAFT, CardStatus.READY),
        (CardStatus.READY, CardStatus.RUNNING),
        (CardStatus.RUNNING, CardStatus.REVIEW),
        (CardStatus.RUNNING, CardStatus.BLOCKED),
        (CardStatus.RUNNING, CardStatus.FAILED),
        (CardStatus.RUNNING, CardStatus.CANCELLED),
        (CardStatus.REVIEW, CardStatus.DONE),
        (CardStatus.REVIEW, CardStatus.REWORK),
        (CardStatus.REWORK, CardStatus.RUNNING),
        (CardStatus.BLOCKED, CardStatus.READY),
        (CardStatus.FAILED, CardStatus.READY),
        (CardStatus.CANCELLED, CardStatus.DRAFT),
    ],
)
def test_legal_card_transitions(current: CardStatus, target: CardStatus) -> None:
    check_transition("card", current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (CardStatus.DRAFT, CardStatus.RUNNING),
        (CardStatus.READY, CardStatus.REVIEW),
        (CardStatus.RUNNING, CardStatus.DONE),
        (CardStatus.BLOCKED, CardStatus.REVIEW),
        (CardStatus.FAILED, CardStatus.DONE),
        (CardStatus.DONE, CardStatus.READY),
        (CardStatus.REVIEW, CardStatus.RUNNING),
        (CardStatus.REVIEW, CardStatus.REVIEW),
    ],
)
def test_illegal_card_transitions_raise(current: CardStatus, target: CardStatus) -> None:
    with pytest.raises(InvalidTransition):
        check_transition("card", current, target)


def test_blocked_card_can_never_reach_review_directly() -> None:
    assert CardStatus.REVIEW not in CARD_TRANSITIONS[CardStatus.BLOCKED]


def test_card_initial_states() -> None:
    assert can_transition("card", None, CardStatus.DRAFT)
    assert can_transition("card", None, CardStatus.READY)
    assert not can_transition("card", None, CardStatus.RUNNING)


def test_run_machine() -> None:
    check_transition("run", None, RunStatus.QUEUED)
    check_transition("run", RunStatus.QUEUED, RunStatus.RUNNING)
    check_transition("run", RunStatus.RUNNING, RunStatus.INTERRUPTED)
    check_transition("run", RunStatus.INTERRUPTED, RunStatus.RUNNING)
    with pytest.raises(InvalidTransition):
        check_transition("run", RunStatus.SUCCEEDED, RunStatus.RUNNING)
    with pytest.raises(InvalidTransition):
        check_transition("run", None, RunStatus.RUNNING)


def test_step_machine() -> None:
    check_transition("step", StepStatus.PENDING, StepStatus.RUNNING)
    check_transition("step", StepStatus.RUNNING, StepStatus.PENDING)  # recovery
    check_transition("step", StepStatus.FAILED, StepStatus.RUNNING)  # retry
    with pytest.raises(InvalidTransition):
        check_transition("step", StepStatus.SUCCEEDED, StepStatus.RUNNING)
    with pytest.raises(InvalidTransition):
        check_transition("step", StepStatus.PENDING, StepStatus.SUCCEEDED)


def test_unknown_machine() -> None:
    with pytest.raises(KeyError):
        check_transition("nope", None, "x")


def test_terminal_states() -> None:
    assert is_terminal_card(CardStatus.DONE)
    assert not is_terminal_card(CardStatus.REVIEW)
    assert is_terminal_run(RunStatus.CANCELLED)
    assert not is_terminal_run(RunStatus.INTERRUPTED)


def test_step_order() -> None:
    assert STEP_ORDER[0] is StepName.PREPARE
    assert STEP_ORDER[-1] is StepName.FINALIZE
    assert len(set(STEP_ORDER)) == 7


def test_autonomy_ordering() -> None:
    assert Autonomy.HIGH.at_least(Autonomy.LOW)
    assert not Autonomy.READ_ONLY.at_least(Autonomy.LOW)
    assert lowest_autonomy(Autonomy.HIGH, Autonomy.LOW, None) is Autonomy.LOW
    assert lowest_autonomy(Autonomy.MEDIUM) is Autonomy.MEDIUM
    with pytest.raises(ValueError, match="at least one"):
        lowest_autonomy(None)
