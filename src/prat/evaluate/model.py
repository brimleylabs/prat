"""What an evaluation finds, and the report it leaves.

A finding is one thing wrong in one place, said so a person or a builder can act on it: which
evaluation and rule, how bad, where, what, and how to fix it when the tool says. A report is
one run of one or more evaluations over a project: the tools that ran (and the ones that could
not, with how to install them) and every finding.

Reports are private to the installation, under ``$PRAT_HOME/evaluations/<project>/``, never in
a repository: a list of a system's open holes is itself something to protect.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SEVERITIES = ("critical", "high", "medium", "low", "info")
DIR = "evaluations"
SHOWN = 12


def rank(severity: str) -> int:
    """0 for the worst. An unknown word ranks with ``info``."""
    return SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES) - 1


def at_least(severity: str, floor: str) -> bool:
    return rank(severity) <= rank(floor)


@dataclass(frozen=True)
class Finding:
    evaluation: str
    rule: str
    severity: str
    title: str
    where: str
    detail: str = ""
    fix: str = ""
    standard: str = ""
    tool: str = ""

    @property
    def key(self) -> str:
        """What makes two findings the same finding across runs: the line a thing is on moves,
        and a number in a message changes, without it being a different fault."""
        place = re.sub(r":\d+(:\d+)?$", "", self.where)
        return f"{self.evaluation}|{self.rule}|{place}"


@dataclass(frozen=True)
class ToolRun:
    """One tool of one evaluation: whether it ran, and if not, why and what would let it."""

    evaluation: str
    tool: str
    ran: bool
    note: str = ""
    seconds: float = 0.0
    findings: int = 0


@dataclass
class Report:
    slug: str
    evaluations: list[str]
    target: str
    id: str = ""
    commit: str = ""
    started: float = 0.0
    seconds: float = 0.0
    tools: list[ToolRun] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def counts(self, evaluation: str | None = None) -> dict[str, int]:
        out = dict.fromkeys(SEVERITIES, 0)
        for finding in self.findings:
            if evaluation is None or finding.evaluation == evaluation:
                out[finding.severity if finding.severity in out else "info"] += 1
        return out

    def worst(self) -> str | None:
        return min((f.severity for f in self.findings), key=rank, default=None)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def from_dict(data: dict[str, Any]) -> Report:
    return Report(
        slug=str(data["slug"]),
        evaluations=[str(e) for e in data.get("evaluations") or []],
        target=str(data.get("target") or ""),
        id=str(data.get("id") or ""),
        commit=str(data.get("commit") or ""),
        started=float(data.get("started") or 0),
        seconds=float(data.get("seconds") or 0),
        tools=[ToolRun(**t) for t in data.get("tools") or []],
        findings=[Finding(**f) for f in data.get("findings") or []],
    )


def folder(home: Path, slug: str) -> Path:
    return home / DIR / slug


def save(home: Path, report: Report) -> Path:
    if not report.id:
        report.id = time.strftime("%Y%m%d-%H%M%S", time.localtime(report.started or time.time()))
    path = folder(home, report.slug) / f"report-{report.id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=1), encoding="utf-8")
    return path


def ids(home: Path, slug: str) -> list[str]:
    """The project's reports, oldest first."""
    found = folder(home, slug).glob("report-*.json")
    return sorted(p.stem.removeprefix("report-") for p in found)


def load(home: Path, slug: str, report_id: str | None = None) -> Report | None:
    """One report, or the newest when no id is given."""
    known = ids(home, slug)
    wanted = report_id or (known[-1] if known else None)
    if wanted is None:
        return None
    try:
        text = (folder(home, slug) / f"report-{wanted}.json").read_text(encoding="utf-8")
        return from_dict(json.loads(text))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def previous(home: Path, report: Report, evaluation: str) -> Report | None:
    """The newest earlier report of the project that ran ``evaluation``."""
    for report_id in reversed(ids(home, report.slug)):
        if report.id and report_id >= report.id:
            continue
        earlier = load(home, report.slug, report_id)
        if earlier is not None and evaluation in earlier.evaluations:
            return earlier
    return None


@dataclass
class Compared:
    """A report against the one before it, for the evaluations both ran."""

    new: list[Finding] = field(default_factory=list)
    still: list[Finding] = field(default_factory=list)
    fixed: list[Finding] = field(default_factory=list)
    against: list[str] = field(default_factory=list)


def compare(home: Path, report: Report) -> Compared:
    out = Compared()
    for evaluation in report.evaluations:
        before = previous(home, report, evaluation)
        mine = [f for f in report.findings if f.evaluation == evaluation]
        if before is None:
            continue
        out.against.append(evaluation)
        was = {f.key: f for f in before.findings if f.evaluation == evaluation}
        now = {f.key for f in mine}
        out.new += [f for f in mine if f.key not in was]
        out.still += [f for f in mine if f.key in was]
        out.fixed += [f for key, f in was.items() if key not in now]
    return out


def by_rule(findings: Iterable[Finding]) -> list[list[Finding]]:
    """Findings grouped by evaluation and rule, the worst and the most numerous first."""
    groups: dict[tuple[str, str], list[Finding]] = {}
    for finding in findings:
        groups.setdefault((finding.evaluation, finding.rule), []).append(finding)
    return sorted(
        groups.values(), key=lambda g: (min(rank(f.severity) for f in g), -len(g), g[0].rule)
    )


def render(
    report: Report,
    compared: Compared | None = None,
    *,
    titles: dict[str, str] | None = None,
    floor: str = "info",
    shown: int = SHOWN,
) -> str:
    """The report as text for a terminal, a message or a file."""
    titles = titles or {}
    lines = [
        f"Evaluation of {report.slug} ({report.target})",
        f"Report {report.id}"
        + (f", commit {report.commit[:12]}" if report.commit else "")
        + f", {report.seconds:.0f} s",
    ]
    for evaluation in report.evaluations:
        counts = report.counts(evaluation)
        total = sum(counts.values())
        said = ", ".join(f"{n} {name}" for name, n in counts.items() if n) or "nothing found"
        lines += ["", f"## {titles.get(evaluation, evaluation.capitalize())}: {total} findings"]
        if total:
            lines.append(said)
        for run in (t for t in report.tools if t.evaluation == evaluation):
            state = f"{run.findings} findings" if run.ran else "NOT RUN"
            note = f" ({run.note})" if run.note else ""
            lines.append(f"- {run.tool}: {state}{note}")
        mine = [
            f for f in report.findings if f.evaluation == evaluation and at_least(f.severity, floor)
        ]
        for group in by_rule(mine):
            head = group[0]
            standard = f" [{head.standard}]" if head.standard else ""
            lines += ["", f"{head.severity.upper()}  {head.title} ({len(group)}){standard}"]
            for finding in group[:shown]:
                detail = f": {finding.detail}" if finding.detail else ""
                lines.append(f"  {finding.where}{detail}")
            if len(group) > shown:
                lines.append(f"  and {len(group) - shown} more")
            if head.fix:
                lines.append(f"  Fix: {head.fix}")
    if compared is not None and compared.against:
        lines += [
            "",
            "Since the last report ("
            + ", ".join(titles.get(e, e) for e in compared.against)
            + f"): {len(compared.new)} new, {len(compared.fixed)} fixed, "
            f"{len(compared.still)} still open.",
        ]
    return "\n".join(lines)


def worst_new(report: Report, compared: Compared | None, floor: str = "critical") -> list[Finding]:
    """Findings bad enough to tell a person about at once: new ones when there is an earlier
    report to compare with, all of them on a first run."""
    pool: Sequence[Finding] = (
        compared.new if compared is not None and compared.against else report.findings
    )
    return [f for f in pool if at_least(f.severity, floor)]
