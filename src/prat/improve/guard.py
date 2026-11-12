"""The self-improvement guard: ``land`` or ``refuse``, from evidence only.

It never asks for a person. Any part of PRAT may be changed by
self-improvement, including this file; what protects the system is that the
evidence is produced by the trusted version (the running PRAT and the
invariant suite from before the change), never by the candidate itself.

``judge`` is a pure function: given the evidence it returns the decision and
every reason for a refusal. Reasons are split into those the card can fix
(rework) and those it cannot (hold and retry later: the remote moved, the
network was down, another trial was running).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from prat.engine.security import SecurityVerdict


class GuardDecision(StrEnum):
    LAND = "land"
    REFUSE = "refuse"


@dataclass(frozen=True)
class Evidence:
    """Everything the guard looks at, gathered by the landing code."""

    branch_head: str
    verified_commit: str | None
    gates_passed: bool
    verify_findings: tuple[str, ...]
    criteria_verdict: str | None
    criteria_commit: str | None
    security_verdict: str | None
    security_commit: str | None
    # The merge result that was proven, and the commit it was proven at.
    merge_commit: str | None = None
    trial_status: str | None = None  # passed, failed, postponed, or None if not run
    trial_summary: str = ""
    remote_diverged: bool = False
    merge_conflict: str | None = None


@dataclass(frozen=True)
class GuardResult:
    decision: GuardDecision
    reasons: tuple[str, ...] = ()
    # True when every reason is outside the card's control: hold and retry.
    hold: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def lands(self) -> bool:
        return self.decision is GuardDecision.LAND

    def feedback(self) -> str:
        """The evidence written as rework feedback for the builder."""
        lines = ["The change was not landed. Fix these and keep everything else as it is:"]
        lines += [f"- {reason}" for reason in self.reasons]
        return "\n".join(lines)


def judge(evidence: Evidence) -> GuardResult:
    fix: list[str] = []
    wait: list[str] = []
    head = evidence.branch_head
    if evidence.verified_commit != head:
        fix.append(
            "the gates did not run on the branch head"
            if evidence.verified_commit
            else "there is no verification for this change"
        )
    elif not evidence.gates_passed:
        fix.append("a required gate failed")
    fix += [f"verification: {finding}" for finding in evidence.verify_findings]
    if evidence.criteria_commit != head or evidence.criteria_verdict != "met":
        fix.append(
            f"the criteria reviewer's verdict for the branch head is "
            f"{evidence.criteria_verdict or 'missing'}, not met"
        )
    if evidence.security_commit != head or evidence.security_verdict != str(SecurityVerdict.PASS):
        fix.append(
            f"the security review of the branch head is "
            f"{evidence.security_verdict or 'missing'}, not pass"
        )
    if evidence.merge_conflict:
        fix.append(f"the branch no longer merges cleanly: {evidence.merge_conflict}")
    if evidence.remote_diverged:
        wait.append("the default branch has diverged from the remote")
    if evidence.trial_status == "failed":
        fix.append(f"the staged trial failed: {evidence.trial_summary}")
    elif evidence.trial_status == "postponed":
        wait.append(f"the staged trial was postponed: {evidence.trial_summary}")
    elif evidence.trial_status != "passed" and not fix and not wait:
        fix.append("the staged trial did not run")
    reasons = tuple(fix + wait)
    if not reasons:
        return GuardResult(GuardDecision.LAND, detail={"merge_commit": evidence.merge_commit})
    return GuardResult(GuardDecision.REFUSE, reasons, hold=not fix)
