"""Core enums and the state machines for cards, runs and run steps.

Every status change in PRAT goes through :func:`check_transition`. The ORM
models call it from attribute validators, so an illegal transition raises no
matter which code path attempts it.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class InvalidTransition(ValueError):
    """Raised when a status change is not allowed by the state machine."""

    def __init__(self, machine: str, current: str | None, target: str) -> None:
        self.machine = machine
        self.current = current
        self.target = target
        super().__init__(f"illegal {machine} transition: {current!r} -> {target!r}")


class CardStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    REVIEW = "review"
    REWORK = "rework"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"
    BLOCKED = "blocked"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class StepName(StrEnum):
    PREPARE = "prepare"
    PLAN = "plan"
    EXECUTE = "execute"
    VERIFY = "verify"
    CRITIQUE = "critique"
    SUMMARIZE = "summarize"
    FINALIZE = "finalize"


STEP_ORDER: tuple[StepName, ...] = (
    StepName.PREPARE,
    StepName.PLAN,
    StepName.EXECUTE,
    StepName.VERIFY,
    StepName.CRITIQUE,
    StepName.SUMMARIZE,
    StepName.FINALIZE,
)


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class Autonomy(StrEnum):
    """How much an agent may do without a human. Ordered from least to most."""

    READ_ONLY = "read_only"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return _AUTONOMY_RANK[self]

    def at_least(self, other: Autonomy) -> bool:
        return self.rank >= other.rank


_AUTONOMY_RANK = {
    Autonomy.READ_ONLY: 0,
    Autonomy.LOW: 1,
    Autonomy.MEDIUM: 2,
    Autonomy.HIGH: 3,
}


def lowest_autonomy(*levels: Autonomy | None) -> Autonomy:
    """Return the most restrictive of the given levels (ignoring ``None``)."""
    present = [level for level in levels if level is not None]
    if not present:
        raise ValueError("at least one autonomy level is required")
    return min(present, key=lambda level: level.rank)


class CardKind(StrEnum):
    """What a card is for. A setup card is the one card allowed to write the project file: its
    agent works out how to install, test and run the project and proposes ``.prat/``."""

    CHANGE = "change"
    SETUP = "setup"


class Complexity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CommentAuthor(StrEnum):
    HUMAN = "human"
    REVIEWER = "reviewer"
    SYSTEM = "system"


class Verdict(StrEnum):
    """The reviewer's overall judgement of a run against the card.

    ``met``: every acceptance criterion is satisfied. ``partial``: some are,
    and a person should decide. ``not_met``: the criteria are not satisfied;
    the card goes back to the builder. ``wrong``: the change does something
    other than what the card asked; also sent back.
    """

    MET = "met"
    PARTIAL = "partial"
    NOT_MET = "not_met"
    WRONG = "wrong"
    # Earlier name for MET, kept so existing callers keep working.
    COMPLETE = "met"

    @property
    def needs_rework(self) -> bool:
        return self in (Verdict.NOT_MET, Verdict.WRONG)


# Self-improvement ------------------------------------------------------------------


class CycleTrigger(StrEnum):
    SCHEDULE = "schedule"
    MANUAL = "manual"


class CycleStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class CycleStage(StrEnum):
    DIAGNOSE = "diagnose"
    RESEARCH = "research"
    SELECT = "select"
    DONE = "done"


class FindingSource(StrEnum):
    DIAGNOSTIC = "diagnostic"
    RESEARCH = "research"
    ROADMAP = "roadmap"


class FindingStatus(StrEnum):
    NEW = "new"
    CARDED = "carded"
    LANDED = "landed"
    DISMISSED = "dismissed"
    STALE = "stale"
    FAILED = "failed"


class Severity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        """0 for the most severe, so sorting ascending puts it first."""
        return ("high", "medium", "low").index(self.value)


class Effort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        """0 for the least effort, so sorting ascending puts it first."""
        return ("low", "medium", "high").index(self.value)


# Legal transitions. A missing key means the state is terminal.
CARD_TRANSITIONS: Mapping[CardStatus, frozenset[CardStatus]] = {
    CardStatus.DRAFT: frozenset({CardStatus.READY, CardStatus.CANCELLED}),
    CardStatus.READY: frozenset({CardStatus.RUNNING, CardStatus.DRAFT, CardStatus.CANCELLED}),
    # running -> rework: the reviewer judged the work not_met or wrong.
    CardStatus.RUNNING: frozenset(
        {
            CardStatus.REVIEW,
            CardStatus.REWORK,
            CardStatus.FAILED,
            CardStatus.BLOCKED,
            CardStatus.CANCELLED,
        }
    ),
    CardStatus.REVIEW: frozenset({CardStatus.DONE, CardStatus.REWORK, CardStatus.CANCELLED}),
    CardStatus.REWORK: frozenset({CardStatus.RUNNING, CardStatus.CANCELLED}),
    CardStatus.BLOCKED: frozenset({CardStatus.READY, CardStatus.REWORK, CardStatus.CANCELLED}),
    CardStatus.FAILED: frozenset({CardStatus.READY, CardStatus.REWORK, CardStatus.CANCELLED}),
    CardStatus.CANCELLED: frozenset({CardStatus.DRAFT}),
    CardStatus.DONE: frozenset(),
}
CARD_INITIAL: frozenset[CardStatus] = frozenset({CardStatus.DRAFT, CardStatus.READY})

RUN_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.BLOCKED,
            RunStatus.CANCELLED,
            RunStatus.INTERRUPTED,
        }
    ),
    RunStatus.INTERRUPTED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.BLOCKED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}
RUN_INITIAL: frozenset[RunStatus] = frozenset({RunStatus.QUEUED})

STEP_TRANSITIONS: Mapping[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PENDING: frozenset({StepStatus.RUNNING, StepStatus.SKIPPED}),
    # RUNNING -> PENDING happens when an interrupted run is recovered.
    StepStatus.RUNNING: frozenset(
        {StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.PENDING}
    ),
    # A failed step may be retried.
    StepStatus.FAILED: frozenset({StepStatus.RUNNING}),
    StepStatus.SUCCEEDED: frozenset(),
    StepStatus.SKIPPED: frozenset(),
}
STEP_INITIAL: frozenset[StepStatus] = frozenset({StepStatus.PENDING})

_Table = dict[str, frozenset[str]]


def _flatten(
    transitions: Mapping[CardStatus, frozenset[CardStatus]]
    | Mapping[RunStatus, frozenset[RunStatus]]
    | Mapping[StepStatus, frozenset[StepStatus]],
) -> _Table:
    return {str(key): frozenset(str(v) for v in values) for key, values in transitions.items()}


_MACHINES: dict[str, tuple[_Table, frozenset[str]]] = {
    "card": (_flatten(CARD_TRANSITIONS), frozenset(str(s) for s in CARD_INITIAL)),
    "run": (_flatten(RUN_TRANSITIONS), frozenset(str(s) for s in RUN_INITIAL)),
    "step": (_flatten(STEP_TRANSITIONS), frozenset(str(s) for s in STEP_INITIAL)),
}


def can_transition(machine: str, current: str | None, target: str) -> bool:
    transitions, initial = _MACHINES[machine]
    if current is None:
        return str(target) in initial
    if current == target:
        return False
    allowed = transitions.get(str(current))
    return allowed is not None and str(target) in allowed


def check_transition(machine: str, current: str | None, target: str) -> None:
    """Raise :class:`InvalidTransition` unless ``current -> target`` is legal."""
    if machine not in _MACHINES:
        raise KeyError(f"unknown state machine {machine!r}")
    if not can_transition(machine, current, target):
        raise InvalidTransition(machine, current, target)


def is_terminal_card(status: CardStatus) -> bool:
    return not CARD_TRANSITIONS[status]


def is_terminal_run(status: RunStatus) -> bool:
    return not RUN_TRANSITIONS[status]
