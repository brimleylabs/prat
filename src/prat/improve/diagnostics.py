"""Diagnostics: how PRAT has been doing lately, measured, with no model cost.

Three parts:

``collect_metrics``
    Numbers over the look-back window, from the database and run journals.
``run_health_checks``
    Each check returns ok, warn or bad with a message. A check that raises is
    reported as bad, never allowed to stop the cycle.
``find_problems``
    Fixed rules over the metrics and checks. Each finding carries the numbers
    as evidence and a stable fingerprint, so the same problem is not filed
    twice across cycles.
"""

from __future__ import annotations

import importlib.metadata
import re
import shutil
import statistics
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from prat.config import Settings
from prat.db import Database, Run, as_utc
from prat.db.migrate import current_revision, head_revision
from prat.db.models import ErrorLog
from prat.domain import Effort, FindingSource, RunStatus, Severity, StepName, StepStatus
from prat.errors import normalise_message
from prat.git import Git
from prat.improve.findings import FindingDraft, finding_fingerprint
from prat.journal import read_journal

# Finding rule thresholds.
ERROR_REPEAT_MIN = 3
ERROR_HIGH_SEVERITY_MIN = 10
GATE_FAIL_RATE = 0.30
GATE_MIN_RUNS = 5
VERDICT_REJECT_RATE = 0.30
VERDICT_MIN_RUNS = 5
POLICY_DENIAL_MIN = 10
LOOP_TRIPS_MIN = 3

# Health check limits.
DISK_BAD_BYTES = 1 * 1024**3
DISK_WARN_BYTES = 5 * 1024**3
AUTH_PROBE_SECONDS = 15.0

REJECTING_VERDICTS = ("not_met", "wrong")
_PATHISH = re.compile(r"\S*[/\\]\S*")


class Health(StrEnum):
    OK = "ok"
    WARN = "warn"
    BAD = "bad"


@dataclass(frozen=True)
class HealthCheck:
    name: str
    state: Health
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "state": str(self.state), "message": self.message}


@dataclass
class Diagnosis:
    metrics: dict[str, Any]
    health: list[HealthCheck]
    findings: list[FindingDraft] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": self.metrics,
            "health": [check.to_dict() for check in self.health],
            "findings": [draft.title for draft in self.findings],
        }


def policy_rule(reason: str) -> str:
    """A denial reason with the parts that vary per call (paths, numbers) removed."""
    return normalise_message(_PATHISH.sub("<path>", reason))


def _journal_path(settings: Settings, run: Run) -> Path:
    return (
        Path(run.journal_path) if run.journal_path else settings.journal_dir / f"run-{run.id}.jsonl"
    )


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 3) if whole else 0.0


# Metrics -------------------------------------------------------------------------


def collect_metrics(
    db: Database, settings: Settings, *, window_days: int, now: datetime | None = None
) -> dict[str, Any]:
    end = now or datetime.now(UTC)
    since = end - timedelta(days=window_days)
    with db.session() as session:
        runs = list(
            session.scalars(
                select(Run)
                .where(Run.created_at >= since)
                .options(selectinload(Run.steps))
                .order_by(Run.id)
            )
        )
        by_status: Counter[str] = Counter(str(run.status) for run in runs)
        step_totals: dict[str, Counter[str]] = defaultdict(Counter)
        gates: dict[str, Counter[str]] = defaultdict(Counter)
        verdicts: Counter[str] = Counter()
        reworks = 0
        budget = 0
        costs: dict[int, float] = defaultdict(float)
        durations: list[float] = []
        policy: Counter[str] = Counter()
        policy_example: dict[str, str] = {}
        loop_trips = 0
        for run in runs:
            costs[run.card_id] += run.cost_usd or 0.0
            started, finished = as_utc(run.started_at), as_utc(run.finished_at)
            if started and finished:
                durations.append((finished - started).total_seconds())
            if run.reason and "budget exceeded" in run.reason:
                budget += 1
            for step in run.steps:
                if step.status in (StepStatus.SUCCEEDED, StepStatus.FAILED):
                    step_totals[str(step.name)][str(step.status)] += 1
                if step.name == StepName.VERIFY:
                    for gate in step.detail.get("gates", []):
                        if not isinstance(gate, dict) or gate.get("cancelled"):
                            continue
                        name = str(gate.get("name"))
                        gates[name]["runs"] += 1
                        if not gate.get("passed"):
                            gates[name]["failed"] += 1
                if step.name == StepName.CRITIQUE and step.status == StepStatus.SUCCEEDED:
                    verdicts[str(step.detail.get("verdict"))] += 1
                    if step.detail.get("rework"):
                        reworks += 1
            for entry in read_journal(_journal_path(settings, run)):
                if entry.kind != "policy" or entry.data.get("allowed", True):
                    continue
                if entry.data.get("loop"):
                    loop_trips += 1
                    continue
                reason = str(entry.data.get("reason", ""))
                rule = policy_rule(reason)
                policy[rule] += 1
                policy_example.setdefault(rule, reason[:300])

        errors: dict[str, dict[str, Any]] = {}
        rows = session.scalars(
            select(ErrorLog).where(ErrorLog.created_at >= since).order_by(ErrorLog.id)
        )
        for row in rows:
            item = errors.setdefault(
                row.fingerprint,
                {"source": row.source, "kind": row.kind, "count": 0, "first_seen": None},
            )
            item["count"] += 1
            seen = as_utc(row.created_at)
            item["last_seen"] = seen.isoformat() if seen else None
            item["first_seen"] = item["first_seen"] or item["last_seen"]
            item["message"] = row.message[:500]

    reviewed = sum(verdicts.values())
    return {
        "window_days": window_days,
        "since": since.isoformat(),
        "runs": {"total": len(runs), "by_status": dict(sorted(by_status.items()))},
        "steps": {
            name: {
                "failed": counts["failed"],
                "total": counts["failed"] + counts["succeeded"],
                "failure_rate": _rate(counts["failed"], counts["failed"] + counts["succeeded"]),
            }
            for name, counts in sorted(step_totals.items())
        },
        "gates": {
            name: {
                "runs": counts["runs"],
                "failed": counts["failed"],
                "failure_rate": _rate(counts["failed"], counts["runs"]),
            }
            for name, counts in sorted(gates.items())
        },
        "verdicts": {"reviewed": reviewed, "by_verdict": dict(sorted(verdicts.items()))},
        "rework_rate": _rate(reworks, reviewed),
        "policy_denials": {
            rule: {"count": count, "example": policy_example[rule]}
            for rule, count in policy.most_common()
        },
        "loop_trips": loop_trips,
        "budget_exhausted": budget,
        "cancelled": by_status.get(str(RunStatus.CANCELLED), 0),
        "interrupted": by_status.get(str(RunStatus.INTERRUPTED), 0),
        "median_cost_per_card_usd": round(statistics.median(costs.values()), 4) if costs else 0.0,
        "median_run_seconds": round(statistics.median(durations), 1) if durations else 0.0,
        "errors": dict(sorted(errors.items(), key=lambda item: -int(item[1]["count"]))),
    }


# Health checks -------------------------------------------------------------------


def _check_database(db: Database, settings: Settings) -> HealthCheck:
    with db.session() as session:
        if session.get_bind().dialect.name == "sqlite":
            result = session.execute(text("PRAGMA quick_check")).scalar()
            if result != "ok":
                return HealthCheck("database", Health.BAD, f"integrity check failed: {result}")
            return HealthCheck("database", Health.OK, "integrity check passed")
        session.execute(text("SELECT 1"))
    return HealthCheck("database", Health.OK, "database answers")


def _check_migrations(db: Database, settings: Settings) -> HealthCheck:
    current, head = current_revision(db), head_revision()
    if current == head:
        return HealthCheck("migrations", Health.OK, f"at head ({head})")
    return HealthCheck("migrations", Health.BAD, f"database is at {current}, code expects {head}")


def _check_git(db: Database, settings: Settings) -> HealthCheck:
    if shutil.which("git") is None:
        return HealthCheck("git", Health.BAD, "git is not on the PATH")
    version = Git(settings.home, timeout=30).out("--version")
    return HealthCheck("git", Health.OK, version)


def _check_sdk(db: Database, settings: Settings) -> HealthCheck:
    try:
        version = importlib.metadata.version("claude-agent-sdk")
    except importlib.metadata.PackageNotFoundError:
        return HealthCheck("agent_sdk", Health.BAD, "the Claude Agent SDK is not installed")
    return HealthCheck("agent_sdk", Health.OK, f"claude-agent-sdk {version}")


def _check_disk(db: Database, settings: Settings) -> HealthCheck:
    free = shutil.disk_usage(settings.home).free
    text_free = f"{free / 1024**3:.1f} GiB free in the PRAT home"
    if free < DISK_BAD_BYTES:
        return HealthCheck("disk", Health.BAD, text_free)
    if free < DISK_WARN_BYTES:
        return HealthCheck("disk", Health.WARN, text_free)
    return HealthCheck("disk", Health.OK, text_free)


def _check_stale_runs(db: Database, settings: Settings) -> HealthCheck:
    limit = datetime.now(UTC) - timedelta(seconds=settings.stale_run_seconds)
    with db.session() as session:
        running = session.scalars(select(Run).where(Run.status == RunStatus.RUNNING))
        stale = [
            run.id for run in running if (beat := as_utc(run.heartbeat_at)) is None or beat < limit
        ]
    if stale:
        ids = ", ".join(str(i) for i in stale[:10])
        return HealthCheck(
            "stale_runs", Health.WARN, f"runs marked running with no heartbeat: {ids}"
        )
    return HealthCheck("stale_runs", Health.OK, "no stale runs")


def _check_journal(db: Database, settings: Settings) -> HealthCheck:
    settings.journal_dir.mkdir(parents=True, exist_ok=True)
    probe = settings.journal_dir / f".probe-{uuid.uuid4().hex}"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    return HealthCheck("journal", Health.OK, "journal folder is writable")


def _check_self_clone(db: Database, settings: Settings) -> HealthCheck:
    from prat.improve.selfproject import get_self_project

    with db.session() as session:
        project = get_self_project(session)
        clone = Path(project.agent_path) if project is not None else None
    if clone is None:
        return HealthCheck("self_clone", Health.WARN, "self-improvement is not set up")
    git = Git(clone, timeout=60)
    if not git.is_repo():
        return HealthCheck("self_clone", Health.BAD, f"the self clone is missing at {clone}")
    if git.is_dirty():
        return HealthCheck("self_clone", Health.WARN, "the self clone has uncommitted changes")
    return HealthCheck("self_clone", Health.OK, "present and clean")


def _auth_check(runtime: str) -> Callable[[Database, Settings], HealthCheck]:
    def check(db: Database, settings: Settings) -> HealthCheck:
        if runtime == "fake":
            return HealthCheck("auth", Health.OK, "the scripted runtime needs no sign-in")
        from prat.runtime.auth import check_auth

        status = check_auth(settings, probe_timeout=AUTH_PROBE_SECONDS)
        return HealthCheck("auth", Health.OK if status.usable else Health.BAD, status.detail)

    return check


def _sync_check(sync: Any) -> HealthCheck:
    if sync.ok:
        return HealthCheck("self_sync", Health.OK, sync.message)
    if sync.diverged:
        return HealthCheck("self_sync", Health.BAD, sync.message)
    return HealthCheck("self_sync", Health.WARN, sync.message)


def run_health_checks(
    db: Database, settings: Settings, *, runtime: str = "claude", sync: Any = None
) -> list[HealthCheck]:
    """Every check, in a fixed order. ``sync`` is the cycle's
    :class:`prat.improve.selfproject.SyncResult`, when it ran one."""
    checks: list[tuple[str, Callable[[Database, Settings], HealthCheck]]] = [
        ("database", _check_database),
        ("migrations", _check_migrations),
        ("git", _check_git),
        ("agent_sdk", _check_sdk),
        ("disk", _check_disk),
        ("auth", _auth_check(runtime)),
        ("self_clone", _check_self_clone),
        ("stale_runs", _check_stale_runs),
        ("journal", _check_journal),
    ]
    results: list[HealthCheck] = []
    for name, check in checks:
        try:
            results.append(check(db, settings))
        except Exception as exc:
            results.append(
                HealthCheck(name, Health.BAD, f"check failed: {type(exc).__name__}: {exc}")
            )
    if sync is not None:
        results.append(_sync_check(sync))
    return results


# Finding rules -------------------------------------------------------------------


def _draft(
    rule: str,
    key: str,
    *,
    title: str,
    problem: str,
    proposal: str,
    criteria: Sequence[str],
    severity: Severity,
    effort: Effort = Effort.MEDIUM,
    evidence: dict[str, Any],
) -> FindingDraft:
    return FindingDraft(
        source=FindingSource.DIAGNOSTIC,
        fingerprint=finding_fingerprint(FindingSource.DIAGNOSTIC, rule, key),
        title=title,
        problem=problem,
        proposal=proposal,
        acceptance_criteria=list(criteria),
        severity=severity,
        effort=effort,
        evidence={"rule": rule, **evidence},
    )


def find_problems(metrics: dict[str, Any], health: Sequence[HealthCheck]) -> list[FindingDraft]:
    window = metrics.get("window_days", 0)
    drafts: list[FindingDraft] = []

    for check in health:
        if check.state is not Health.BAD:
            continue
        drafts.append(
            _draft(
                "health",
                check.name,
                title=f"Health check failing: {check.name}",
                problem=f"The {check.name} health check reports: {check.message}",
                proposal=(
                    f"Find why the {check.name} check fails and fix the cause. If the cause is "
                    "outside PRAT, make PRAT detect it earlier and explain the fix to the person."
                ),
                criteria=[f"the {check.name} health check reports ok in a fresh installation"],
                severity=Severity.HIGH,
                effort=Effort.LOW,
                evidence={"check": check.to_dict()},
            )
        )

    for fp, error in metrics.get("errors", {}).items():
        count = int(error["count"])
        if count < ERROR_REPEAT_MIN:
            continue
        drafts.append(
            _draft(
                "error",
                fp,
                title=f"Repeated {error['source']} error: {error['kind']}",
                problem=(
                    f"PRAT recorded this {error['source']} error {count} times in the last "
                    f"{window} days (last at {error.get('last_seen')}): {error.get('message', '')}"
                ),
                proposal=(
                    "Find the cause from the error log entry and the code path that raises it, "
                    "fix it, and add a test that reproduces the original failure."
                ),
                criteria=[
                    "a test reproduces the original failure and passes after the fix",
                    "the error is handled or prevented at its cause, not silenced",
                ],
                severity=Severity.HIGH if count >= ERROR_HIGH_SEVERITY_MIN else Severity.MEDIUM,
                evidence={"error_fingerprint": fp, **error},
            )
        )

    for name, gate in metrics.get("gates", {}).items():
        runs, failed = int(gate["runs"]), int(gate["failed"])
        if runs < GATE_MIN_RUNS or failed / runs < GATE_FAIL_RATE:
            continue
        drafts.append(
            _draft(
                "gate",
                name,
                title=f"Gate '{name}' fails in {failed} of {runs} runs",
                problem=(
                    f"The '{name}' gate failed in {failed} of {runs} runs over the last {window} "
                    "days. Builders are finishing work that does not pass it."
                ),
                proposal=(
                    f"Look at the failing '{name}' gate outputs in recent runs and change what "
                    "builders are told or given (prompt, tools, checks before finishing) so the "
                    "gate passes more often."
                ),
                criteria=[
                    f"builders are told how to run the '{name}' gate before they finish",
                    "a scripted run shows the new guidance reaching the builder",
                ],
                severity=Severity.MEDIUM,
                evidence={"gate": name, **gate},
            )
        )

    reviewed = int(metrics.get("verdicts", {}).get("reviewed", 0))
    by_verdict = metrics.get("verdicts", {}).get("by_verdict", {})
    rejected = sum(int(by_verdict.get(v, 0)) for v in REJECTING_VERDICTS)
    if reviewed >= VERDICT_MIN_RUNS and rejected / reviewed >= VERDICT_REJECT_RATE:
        drafts.append(
            _draft(
                "verdict",
                "rejections",
                title=f"Reviewer rejects {rejected} of {reviewed} runs",
                problem=(
                    f"{rejected} of {reviewed} reviewed runs in the last {window} days were "
                    "judged not_met or wrong."
                ),
                proposal=(
                    "Compare rejected runs with their acceptance criteria and improve what the "
                    "builder is given (criteria in the prompt, planning, a self-check against "
                    "the criteria before finishing)."
                ),
                criteria=["the builder checks its work against each acceptance criterion"],
                severity=Severity.MEDIUM,
                evidence={"reviewed": reviewed, "rejected": rejected, "by_verdict": by_verdict},
            )
        )

    for rule, denial in metrics.get("policy_denials", {}).items():
        count = int(denial["count"])
        if count < POLICY_DENIAL_MIN:
            continue
        drafts.append(
            _draft(
                "policy",
                rule,
                title=f"Policy denied {count} calls: {rule[:80]}",
                problem=(
                    f"The policy denied {count} tool calls for the same reason in the last "
                    f"{window} days, for example: {denial['example']}"
                ),
                proposal=(
                    "Find out why agents keep trying this. Either tell them up front what is not "
                    "allowed and what to do instead, or, when it is a safe missing capability, "
                    "provide it. Never loosen a policy rule that protects isolation, secrets or "
                    "the push block."
                ),
                criteria=["agents are told about this rule before they hit it"],
                severity=Severity.LOW,
                evidence={"policy_rule": rule, **denial},
            )
        )

    loops = int(metrics.get("loop_trips", 0))
    if loops >= LOOP_TRIPS_MIN:
        drafts.append(
            _draft(
                "loops",
                "loop_trips",
                title=f"Loop detector tripped {loops} times",
                problem=(
                    f"Agents repeated the same tool call until stopped {loops} times "
                    f"in the last {window} days."
                ),
                proposal=(
                    "Look at the journals where the loop detector tripped and change the prompt "
                    "or tool feedback so agents notice they are repeating themselves earlier."
                ),
                criteria=["the denial an agent gets on a loop tells it what to try instead"],
                severity=Severity.MEDIUM,
                evidence={"loop_trips": loops},
            )
        )
    return drafts


def diagnose(
    db: Database,
    settings: Settings,
    *,
    window_days: int,
    runtime: str = "claude",
    sync: Any = None,
    now: datetime | None = None,
) -> Diagnosis:
    metrics = collect_metrics(db, settings, window_days=window_days, now=now)
    health = run_health_checks(db, settings, runtime=runtime, sync=sync)
    return Diagnosis(metrics, health, find_problems(metrics, health))
