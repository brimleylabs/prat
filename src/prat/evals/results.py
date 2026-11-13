"""Evaluation results: what each attempt did, what the review layer decided, and whether the
hidden check agreed.

Every attempt that the hidden check judged is one of four kinds:

``caught``
    The work fails the hidden check, and PRAT sent it back (a gate failed or the reviewer
    asked for rework).
``missed``
    The work fails the hidden check, and PRAT handed it over for approval.
``false_alarm``
    The work passes the hidden check, and PRAT sent it back anyway.
``passed``
    The work passes the hidden check, and PRAT handed it over.

A case compares the agent's first attempt (what the agent alone would have handed over)
with what PRAT finally handed over: ``fixed`` (bad first attempt, good work handed over),
``kept`` (good, and handed over), ``stopped`` (bad, and nothing handed over), ``lost``
(good first attempt, nothing handed over) and ``missed`` (bad work handed over).

Results are JSON files under ``$PRAT_HOME/evals/<id>/result.json``; everything else is
computed from them, so two runs can be compared at any time.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from prat.domain import STEP_ORDER, CardStatus
from prat.evals.cases import RecordedRun

FORMAT = 1
RESULT_FILE = "result.json"
CALLS = ("caught", "missed", "false_alarm", "passed")
OUTCOMES = ("fixed", "kept", "stopped", "lost", "missed", "handed_over", "withheld", "error")


class GateOutcome(BaseModel):
    name: str
    passed: bool
    output: str = ""


class CheckOutcome(BaseModel):
    passed: bool
    exit_code: int | None = None
    timed_out: bool = False
    output: str = ""


class PolicyCount(BaseModel):
    calls: int = 0
    denied: int = 0


class Attempt(BaseModel):
    number: int
    run_id: int | None = None
    run_status: str | None = None
    card_status: str
    reason: str | None = None
    steps: dict[str, str] = Field(default_factory=dict)
    gates: list[GateOutcome] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    verdict: str | None = None
    commit: str | None = None
    check: CheckOutcome | None = None
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    policy: PolicyCount = Field(default_factory=PolicyCount)

    @property
    def accepted(self) -> bool:
        """PRAT handed this attempt over for a person's approval."""
        return self.card_status == CardStatus.REVIEW

    @property
    def call(self) -> str | None:
        """caught, missed, false_alarm or passed; ``None`` without a hidden check or when
        the run ended without a judgement (blocked, cancelled, an agent error)."""
        if self.check is None:
            return None
        bad = not self.check.passed
        if self.accepted:
            return "missed" if bad else "passed"
        if self.card_status == CardStatus.REWORK or (
            self.card_status == CardStatus.FAILED and self.steps.get("verify") == "failed"
        ):
            return "caught" if bad else "false_alarm"
        return None

    def trail(self) -> str:
        """One short phrase: what stopped the attempt, or the verdict."""
        failed = [gate.name for gate in self.gates if not gate.passed]
        if failed:
            return f"{', '.join(failed)} failed"
        if self.findings and self.steps.get("verify") == "failed":
            return "verify failed"
        if self.verdict:
            return self.verdict
        return self.card_status


class CaseResult(BaseModel):
    name: str
    title: str
    project: str = ""
    has_check: bool = False
    attempts: list[Attempt] = Field(default_factory=list)
    # How the card's runs went when it was recorded, for an exported case.
    recorded: list[RecordedRun] | None = None
    error: str | None = None

    @property
    def first(self) -> Attempt | None:
        return self.attempts[0] if self.attempts else None

    @property
    def delivered(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].accepted

    @property
    def baseline_pass(self) -> bool | None:
        """Whether the first attempt passes the hidden check."""
        first = self.first
        if first is None or first.check is None:
            return None
        return first.check.passed

    @property
    def delivered_pass(self) -> bool | None:
        """Whether PRAT handed over work that passes the hidden check."""
        if not self.has_check or not self.attempts:
            return None
        last = self.attempts[-1]
        return self.delivered and last.check is not None and last.check.passed

    @property
    def outcome(self) -> str:
        if self.error is not None or not self.attempts:
            return "error"
        base, good = self.baseline_pass, self.delivered_pass
        if base is None or good is None:
            return "handed_over" if self.delivered else "withheld"
        if self.delivered:
            if not good:
                return "missed"
            return "kept" if base else "fixed"
        return "lost" if base else "stopped"

    @property
    def cost_usd(self) -> float:
        return sum(attempt.cost_usd for attempt in self.attempts)


class EvalResult(BaseModel):
    format: int = FORMAT
    id: str
    label: str = ""
    created_at: str
    prat_version: str
    suite: str
    runtime: str
    reviewer: str
    sandbox: str = "off"
    overlay: bool = False
    # PRAT's agents had the project's seeded knowledge.
    knowledge: bool = False
    models: dict[str, str] = Field(default_factory=dict)
    duration_seconds: float = 0.0
    cases: list[CaseResult] = Field(default_factory=list)

    @property
    def scripted(self) -> bool:
        return self.runtime == "scripted" and self.reviewer == "scripted"


# Arithmetic ----------------------------------------------------------------------------------


@dataclass
class Summary:
    cases: int = 0
    errors: int = 0
    checked: int = 0
    baseline_pass: int = 0
    delivered: int = 0
    delivered_pass: int = 0
    delivered_bad: int = 0
    calls: Counter[str] = field(default_factory=Counter)
    outcomes: Counter[str] = field(default_factory=Counter)
    cost_usd: float = 0.0

    @property
    def baseline_bad(self) -> int:
        """Checked cases whose first attempt fails: what the agent alone hands over bad."""
        return self.checked - self.baseline_pass

    @property
    def catch_rate(self) -> float | None:
        judged = self.calls["caught"] + self.calls["missed"]
        return self.calls["caught"] / judged if judged else None

    @property
    def false_alarm_rate(self) -> float | None:
        judged = self.calls["false_alarm"] + self.calls["passed"]
        return self.calls["false_alarm"] / judged if judged else None


def summarize(cases: list[CaseResult]) -> Summary:
    summary = Summary()
    for case in cases:
        summary.cases += 1
        summary.outcomes[case.outcome] += 1
        summary.cost_usd += case.cost_usd
        if case.outcome == "error":
            summary.errors += 1
            continue
        if case.delivered:
            summary.delivered += 1
        for attempt in case.attempts:
            if attempt.call is not None:
                summary.calls[attempt.call] += 1
        if case.baseline_pass is None:
            continue
        summary.checked += 1
        summary.baseline_pass += int(bool(case.baseline_pass))
        if case.delivered:
            if case.delivered_pass:
                summary.delivered_pass += 1
            else:
                summary.delivered_bad += 1
    return summary


@dataclass(frozen=True)
class StepRate:
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def rate(self) -> float | None:
        ran = self.succeeded + self.failed
        return self.succeeded / ran if ran else None


def step_rates(cases: list[CaseResult]) -> dict[str, StepRate]:
    """Per step, across every run: how often it succeeded, failed or was skipped."""
    counts: dict[str, Counter[str]] = {str(step): Counter() for step in STEP_ORDER}
    for case in cases:
        for attempt in case.attempts:
            for step, status in attempt.steps.items():
                counts.setdefault(step, Counter())[status] += 1
    return {
        step: StepRate(c["succeeded"], c["failed"], c["skipped"])
        for step, c in counts.items()
        if sum(c.values())
    }


def by_project(cases: list[CaseResult]) -> dict[str, Summary]:
    projects: dict[str, list[CaseResult]] = {}
    for case in cases:
        projects.setdefault(case.project or case.name, []).append(case)
    return {name: summarize(group) for name, group in sorted(projects.items())}


def percent(part: int, whole: int) -> str:
    return f"{part} of {whole} ({round(100 * part / whole)}%)" if whole else "none"


def rate_text(value: float | None) -> str:
    return "n/a" if value is None else f"{round(100 * value)}%"


# Storage ------------------------------------------------------------------------------------


def evals_dir(home: Path) -> Path:
    return home / "evals"


def new_id(label: str = "") -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    suffix = os.urandom(2).hex()
    clean = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in label.lower()).strip("-")
    return f"{stamp}-{suffix}" + (f"-{clean[:30]}" if clean else "")


def save(result: EvalResult, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / RESULT_FILE
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load(path: Path) -> EvalResult:
    file = path / RESULT_FILE if path.is_dir() else path
    data = json.loads(file.read_text(encoding="utf-8"))
    if data.get("format") != FORMAT:
        raise ValueError(f"{file}: result format {data.get('format')!r}, expected {FORMAT}")
    return EvalResult.model_validate(data)


def list_results(home: Path) -> list[EvalResult]:
    """Stored results, newest first. Unreadable folders are left out."""
    folder = evals_dir(home)
    if not folder.is_dir():
        return []
    found: list[EvalResult] = []
    for child in folder.iterdir():
        if (child / RESULT_FILE).is_file():
            try:
                found.append(load(child))
            except (OSError, ValueError):
                continue
    return sorted(found, key=lambda result: result.id, reverse=True)


def find(home: Path, ref: str) -> EvalResult:
    """A stored result by id, a unique start of an id, ``latest``, ``previous``, or a path."""
    candidate = Path(ref).expanduser()
    if candidate.exists():
        return load(candidate)
    results = list_results(home)
    if ref in ("latest", "previous"):
        index = 0 if ref == "latest" else 1
        if len(results) <= index:
            raise LookupError(f"there is no {ref} evaluation")
        return results[index]
    matches = [result for result in results if result.id == ref] or [
        result for result in results if result.id.startswith(ref)
    ]
    if not matches:
        raise LookupError(f"no evaluation {ref!r}; see `prat eval list`")
    if len(matches) > 1:
        raise LookupError(f"{ref!r} matches {len(matches)} evaluations; give more of the id")
    return matches[0]


# Comparison ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    label: str
    a: str
    b: str

    @property
    def changed(self) -> bool:
        return self.a != self.b


@dataclass(frozen=True)
class Comparison:
    a: EvalResult
    b: EvalResult
    review: list[Row]
    steps: list[Row]
    cases: list[Row]
    only_a: list[str]
    only_b: list[str]


def _case_text(case: CaseResult | None) -> str:
    if case is None:
        return "-"
    trail = " > ".join(attempt.trail() for attempt in case.attempts) or (case.error or "")
    return f"{case.outcome}: {trail}" if trail else case.outcome


def compare(a: EvalResult, b: EvalResult) -> Comparison:
    """Two evaluations side by side, over the cases both ran."""
    names_a = {case.name: case for case in a.cases}
    names_b = {case.name: case for case in b.cases}
    common = [name for name in names_a if name in names_b]
    shared_a = [names_a[name] for name in common]
    shared_b = [names_b[name] for name in common]
    sa, sb = summarize(shared_a), summarize(shared_b)
    review = [
        Row(
            "first attempt passes the hidden check",
            percent(sa.baseline_pass, sa.checked),
            percent(sb.baseline_pass, sb.checked),
        ),
        Row(
            "PRAT hands over work that passes",
            percent(sa.delivered_pass, sa.checked),
            percent(sb.delivered_pass, sb.checked),
        ),
        Row("bad work handed over", str(sa.delivered_bad), str(sb.delivered_bad)),
        Row("bad attempts caught", rate_text(sa.catch_rate), rate_text(sb.catch_rate)),
        Row(
            "good attempts sent back",
            rate_text(sa.false_alarm_rate),
            rate_text(sb.false_alarm_rate),
        ),
        Row("cost (USD)", f"{sa.cost_usd:.2f}", f"{sb.cost_usd:.2f}"),
    ]
    rates_a, rates_b = step_rates(shared_a), step_rates(shared_b)
    steps = [
        Row(
            step,
            rate_text(rates_a[step].rate) if step in rates_a else "-",
            rate_text(rates_b[step].rate) if step in rates_b else "-",
        )
        for step in [str(s) for s in STEP_ORDER]
        if step in rates_a or step in rates_b
    ]
    cases = [Row(name, _case_text(names_a[name]), _case_text(names_b[name])) for name in common]
    return Comparison(
        a,
        b,
        review,
        steps,
        cases,
        sorted(set(names_a) - set(names_b)),
        sorted(set(names_b) - set(names_a)),
    )
