"""Pluggable steps: the reviewer (critique) and the planner (plan).

Agent-backed implementations live in :mod:`prat.engine.critic` and
:mod:`prat.engine.planner`; they run in a fresh session with read-only tools
and judge the diff and gate evidence rather than the builder's own account.
:class:`FakeReviewer` and :class:`NoPlanner` are deterministic stand-ins.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from prat.domain import Verdict

if TYPE_CHECKING:
    from prat.projects import ProjectSpec
    from prat.runtime.base import PolicyCallback, RuntimeEvent


@dataclass(frozen=True)
class ReviewRequest:
    card_id: int
    title: str
    goal: str
    acceptance_criteria: str
    diff: str
    diff_stats: dict[str, Any]
    gates: list[dict[str, Any]]
    journal_path: str | None = None
    # Summary computed from the diff (never the builder's own claims).
    summary: str = ""
    # Worktree the reviewer may read, and the read-only policy guarding it.
    workdir: str | None = None
    policy: PolicyCallback | None = None
    model: str | None = None
    budget_usd: float | None = None
    # Feedback the run under review had to address, if it was a rework.
    feedback: str | None = None
    # The change classification (prat.changes.ChangeSet.to_dict()) and the commit
    # under review. The verdict is bound to that commit.
    changes: dict[str, Any] | None = None
    head_commit: str | None = None
    # Changes on the card's database branches (verify step "databases").
    databases: list[dict[str, Any]] | None = None
    # Receives the reviewer session's events (the engine journals them).
    on_event: Callable[[RuntimeEvent], None] | None = None
    # What PRAT has learned about the project, already selected for this card.
    knowledge: str = ""


@dataclass(frozen=True)
class CriterionFinding:
    criterion: str
    status: str  # met, partial or not_met
    evidence: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"criterion": self.criterion, "status": self.status, "evidence": self.evidence}


_STATUS_MARK = {"met": "[met]", "partial": "[partial]", "not_met": "[not met]"}


@dataclass(frozen=True)
class ReviewResult:
    verdict: Verdict
    summary: str
    concerns: list[str] = field(default_factory=list)
    confidence: float | None = None
    findings: list[CriterionFinding] = field(default_factory=list)
    fix_list: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    # False when the reviewer's answer could not be parsed as a verdict.
    parsed: bool = True

    @property
    def needs_rework(self) -> bool:
        return self.verdict.needs_rework

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": str(self.verdict),
            "summary": self.summary,
            "concerns": list(self.concerns),
            "confidence": self.confidence,
            "findings": [f.to_dict() for f in self.findings],
            "fix_list": list(self.fix_list),
            "cost_usd": round(self.cost_usd, 6),
            "parsed": self.parsed,
        }

    def render(self) -> str:
        lines = [f"Verdict: {self.verdict}", "", self.summary]
        if self.findings:
            lines += ["", "Acceptance criteria:"]
            for finding in self.findings:
                mark = _STATUS_MARK.get(finding.status, f"[{finding.status}]")
                text = f"- {mark} {finding.criterion}"
                if finding.evidence:
                    text += f": {finding.evidence}"
                lines.append(text)
        if self.fix_list:
            lines += ["", "Fix list:", *[f"- {item}" for item in self.fix_list]]
        if self.concerns:
            lines += ["", "Concerns:", *[f"- {c}" for c in self.concerns]]
        return "\n".join(lines).strip()

    def render_fix_list(self) -> str:
        items = self.fix_list or [
            f.criterion for f in self.findings if f.status in ("not_met", "partial")
        ]
        lines = [
            f"Reviewer verdict {self.verdict}: sent back for rework.",
            "",
            self.summary,
        ]
        if items:
            lines += ["", "Fix list:", *[f"- {item}" for item in items]]
        return "\n".join(lines).strip()


@runtime_checkable
class Reviewer(Protocol):
    async def review(self, request: ReviewRequest) -> ReviewResult: ...


@dataclass(frozen=True)
class PlanRequest:
    card_id: int
    title: str
    goal: str
    acceptance_criteria: str
    complexity: str
    workdir: str
    spec: ProjectSpec | None = None
    policy: PolicyCallback | None = None
    model: str | None = None
    budget_usd: float | None = None
    on_event: Callable[[RuntimeEvent], None] | None = None
    # What PRAT has learned about the project, already selected for this card.
    knowledge: str = ""


@dataclass(frozen=True)
class PlanResult:
    """What a planner produced. ``blocked_reason`` set means the card is blocked."""

    plan: str | None = None
    blocked_reason: str | None = None
    cost_usd: float = 0.0
    error: str | None = None


@runtime_checkable
class Planner(Protocol):
    async def plan(self, request: PlanRequest) -> PlanResult | str | None:
        """Return a plan for the builder, a blocked result, or ``None`` to skip."""
        ...


class NoPlanner:
    async def plan(self, request: PlanRequest) -> PlanResult | str | None:
        return None


class FakeReviewer:
    """Returns a fixed verdict. Records every request for tests."""

    def __init__(
        self,
        verdict: Verdict = Verdict.MET,
        summary: str = "Scripted review.",
        concerns: list[str] | None = None,
        fix_list: list[str] | None = None,
    ) -> None:
        self.verdict = verdict
        self.summary = summary
        self.concerns = list(concerns or [])
        self.fix_list = list(fix_list or [])
        self.requests: list[ReviewRequest] = []

    async def review(self, request: ReviewRequest) -> ReviewResult:
        self.requests.append(request)
        return ReviewResult(
            self.verdict,
            self.summary,
            list(self.concerns),
            confidence=1.0,
            fix_list=list(self.fix_list),
        )
