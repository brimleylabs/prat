"""Diagnostics: metrics, health checks, finding rules and the findings store."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import select

from conftest import WRITE_GREET, add_card, make_engine, project_toml
from prat.config import Settings
from prat.db import Database, Project, Run
from prat.db.models import ImproveEvent, ImproveFinding
from prat.domain import FindingSource, FindingStatus, Severity, Verdict
from prat.engine import FakeReviewer
from prat.errors import record_error
from prat.improve import diagnostics
from prat.improve.diagnostics import (
    Health,
    HealthCheck,
    collect_metrics,
    diagnose,
    find_problems,
    policy_rule,
    run_health_checks,
)
from prat.improve.findings import (
    FindingDraft,
    dismiss_finding,
    finding_fingerprint,
    mark_stale,
    record_findings,
)
from prat.journal import Journal

pytestmark = pytest.mark.integration


def metrics(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "window_days": 7,
        "gates": {},
        "verdicts": {"reviewed": 0, "by_verdict": {}},
        "policy_denials": {},
        "loop_trips": 0,
        "errors": {},
    }
    base.update(overrides)
    return base


def rules(drafts: list[FindingDraft]) -> list[str]:
    return [str(d.evidence["rule"]) for d in drafts]


def last_run(db: Database) -> Run:
    with db.session() as session:
        run = session.scalars(select(Run).order_by(Run.id.desc())).first()
        assert run is not None
        session.expunge(run)
        return run


# Metrics from real runs -----------------------------------------------------------


async def test_metrics_count_runs_gates_and_verdicts(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    good = project_factory("good")
    bad = project_factory("bad", project_toml=project_toml(test_code="import sys; sys.exit(1)"))
    engine, _ = make_engine(settings, db, WRITE_GREET, reviewer=FakeReviewer(Verdict.PARTIAL))
    await engine.run_card(add_card(db, good))
    await engine.run_card(add_card(db, bad))

    snapshot = collect_metrics(db, settings, window_days=7)
    assert snapshot["runs"]["total"] == 2
    assert snapshot["runs"]["by_status"] == {"failed": 1, "succeeded": 1}
    assert snapshot["gates"]["test"] == {"runs": 2, "failed": 1, "failure_rate": 0.5}
    assert snapshot["steps"]["verify"]["failed"] == 1
    assert snapshot["verdicts"] == {"reviewed": 1, "by_verdict": {"partial": 1}}
    assert snapshot["median_run_seconds"] >= 0


async def test_policy_denials_and_loop_trips_come_from_the_journals(
    settings: Settings, db: Database, project: Project
) -> None:
    engine, _ = make_engine(settings, db, WRITE_GREET)
    await engine.run_card(add_card(db, project))
    run = last_run(db)
    journal = Journal(settings.journal_dir / f"run-{run.id}.jsonl")
    for path in ("/srv/a/secret.txt", "/home/x/other.txt", "C:\\data\\y.txt"):
        journal.append(
            "policy",
            {"allowed": False, "reason": f"{path} is outside the card worktree", "loop": False},
        )
    journal.append("policy", {"allowed": False, "reason": "same call repeated", "loop": True})

    snapshot = collect_metrics(db, settings, window_days=7)
    assert snapshot["loop_trips"] == 1
    assert snapshot["policy_denials"] == {
        "<path> is outside the card worktree": {
            "count": 3,
            "example": "/srv/a/secret.txt is outside the card worktree",
        }
    }


def test_errors_are_grouped_by_fingerprint(settings: Settings, db: Database) -> None:
    for block in (11, 22, 33):
        record_error(db, "engine", "OSError", f"disk read failed at block {block}")
    record_error(db, "web", "KeyError", "missing 'x'")
    errors = collect_metrics(db, settings, window_days=7)["errors"]
    counts = sorted(int(e["count"]) for e in errors.values())
    assert counts == [1, 3]
    drafts = find_problems(collect_metrics(db, settings, window_days=7), [])
    assert rules(drafts) == ["error"]
    assert drafts[0].title == "Repeated engine error: OSError"
    assert drafts[0].severity is Severity.MEDIUM


def test_the_window_excludes_old_rows(settings: Settings, db: Database) -> None:
    from datetime import UTC, datetime, timedelta

    for _ in range(3):
        record_error(db, "engine", "OSError", "old failure")
    later = datetime.now(UTC) + timedelta(days=30)
    assert collect_metrics(db, settings, window_days=7, now=later)["errors"] == {}


# Finding rules ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("gate", "expected"),
    [
        ({"runs": 5, "failed": 2, "failure_rate": 0.4}, ["gate"]),
        ({"runs": 10, "failed": 3, "failure_rate": 0.3}, ["gate"]),
        ({"runs": 10, "failed": 2, "failure_rate": 0.2}, []),
        ({"runs": 4, "failed": 4, "failure_rate": 1.0}, []),
    ],
)
def test_gate_rule(gate: dict[str, Any], expected: list[str]) -> None:
    assert rules(find_problems(metrics(gates={"test": gate}), [])) == expected


@pytest.mark.parametrize(
    ("by_verdict", "expected"),
    [
        ({"met": 3, "not_met": 1, "wrong": 1}, ["verdict"]),
        ({"met": 4, "not_met": 1}, []),
        ({"not_met": 4}, []),
    ],
)
def test_verdict_rule(by_verdict: dict[str, int], expected: list[str]) -> None:
    reviewed = sum(by_verdict.values())
    drafts = find_problems(metrics(verdicts={"reviewed": reviewed, "by_verdict": by_verdict}), [])
    assert rules(drafts) == expected


def test_policy_and_loop_rules() -> None:
    denials = {
        "<path> is outside the card worktree": {"count": 10, "example": "x"},
        "git push is not allowed": {"count": 9, "example": "y"},
    }
    drafts = find_problems(metrics(policy_denials=denials, loop_trips=3), [])
    assert rules(drafts) == ["policy", "loops"]
    assert metrics(loop_trips=2) and rules(find_problems(metrics(loop_trips=2), [])) == []


def test_only_bad_health_checks_become_findings() -> None:
    health = [
        HealthCheck("disk", Health.WARN, "4.0 GiB free"),
        HealthCheck("git", Health.BAD, "git is not on the PATH"),
        HealthCheck("database", Health.OK, "fine"),
    ]
    (draft,) = find_problems(metrics(), health)
    assert draft.title == "Health check failing: git"
    assert draft.severity is Severity.HIGH


def test_error_severity_rises_with_count() -> None:
    errors = {"f" * 64: {"source": "engine", "kind": "E", "count": 10, "message": "m"}}
    (draft,) = find_problems(metrics(errors=errors), [])
    assert draft.severity is Severity.HIGH


def test_fingerprints_are_stable_and_keyed() -> None:
    gates = {
        "test": {"runs": 5, "failed": 5, "failure_rate": 1.0},
        "lint": {"runs": 5, "failed": 5, "failure_rate": 1.0},
    }
    first = find_problems(metrics(gates=gates), [])
    again = find_problems(metrics(gates={**gates, "test": {**gates["test"], "failed": 4}}), [])
    assert [d.fingerprint for d in first] == [d.fingerprint for d in again]
    assert len({d.fingerprint for d in first}) == 2


def test_policy_rule_ignores_paths_and_numbers() -> None:
    assert policy_rule("/a/b.txt is outside the card worktree") == policy_rule(
        "D:\\x\\y.txt is outside the card worktree"
    )
    assert policy_rule("command 12 denied") == policy_rule("command 99 denied")


# Health checks ----------------------------------------------------------------------


def test_health_checks_on_a_fresh_install(settings: Settings, db: Database) -> None:
    checks = {c.name: c for c in run_health_checks(db, settings, runtime="fake")}
    assert list(checks) == [
        "database",
        "migrations",
        "git",
        "agent_sdk",
        "disk",
        "auth",
        "self_clone",
        "stale_runs",
        "journal",
    ]
    for name in ("database", "migrations", "git", "agent_sdk", "auth", "stale_runs", "journal"):
        assert checks[name].state is Health.OK, checks[name]
    assert checks["self_clone"].state is Health.WARN
    assert "not set up" in checks["self_clone"].message


def test_a_check_that_crashes_is_reported_bad(
    settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_path: object) -> object:
        raise OSError("device not ready")

    monkeypatch.setattr(diagnostics.shutil, "disk_usage", broken)
    checks = {c.name: c for c in run_health_checks(db, settings, runtime="fake")}
    assert checks["disk"].state is Health.BAD
    assert "device not ready" in checks["disk"].message


def test_low_disk_is_a_warning_then_bad(
    settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Usage:
        def __init__(self, free: int) -> None:
            self.free = free

    for free, state in ((3 * 1024**3, Health.WARN), (512 * 1024**2, Health.BAD)):
        monkeypatch.setattr(diagnostics.shutil, "disk_usage", lambda _p, f=free: Usage(f))
        checks = {c.name: c for c in run_health_checks(db, settings, runtime="fake")}
        assert checks["disk"].state is state


def test_the_sync_result_is_a_health_check(settings: Settings, db: Database) -> None:
    from prat.improve.selfproject import SyncResult

    diverged = SyncResult(False, True, "abc", "main has diverged")
    checks = {c.name: c for c in run_health_checks(db, settings, runtime="fake", sync=diverged)}
    assert checks["self_sync"].state is Health.BAD


def test_diagnose_puts_it_together(settings: Settings, db: Database) -> None:
    for _ in range(3):
        record_error(db, "gate", "gate_not_started", "gate 'test' could not start")
    result = diagnose(db, settings, window_days=7, runtime="fake")
    assert rules(result.findings) == ["error"]
    assert result.to_dict()["health"][0]["name"] == "database"


# The findings store -----------------------------------------------------------------


def draft(key: str = "a", **overrides: Any) -> FindingDraft:
    fields: dict[str, Any] = {
        "source": FindingSource.DIAGNOSTIC,
        "fingerprint": finding_fingerprint(FindingSource.DIAGNOSTIC, "test", key),
        "title": f"finding {key}",
        "problem": "p",
        "proposal": "q",
        "acceptance_criteria": ["one", "two"],
        "evidence": {"n": 1},
    }
    fields.update(overrides)
    return FindingDraft(**fields)


def rows(db: Database) -> list[ImproveFinding]:
    with db.session() as session:
        found = list(session.scalars(select(ImproveFinding).order_by(ImproveFinding.id)))
        for row in found:
            session.expunge(row)
        return found


def recorded_events(db: Database) -> list[str]:
    with db.session() as session:
        return list(session.scalars(select(ImproveEvent.kind).order_by(ImproveEvent.id)))


def test_a_finding_seen_again_is_one_row(db: Database) -> None:
    with db.session() as session:
        (first,) = record_findings(session, [draft()])
    with db.session() as session:
        (second,) = record_findings(session, [draft(evidence={"n": 2}, problem="newer")])
    assert first.new and not second.new
    assert first.finding_id == second.finding_id
    (row,) = rows(db)
    assert row.seen_count == 2
    assert row.evidence == {"n": 2}
    assert row.problem == "newer"
    assert row.acceptance_criteria == "- one\n- two"
    assert recorded_events(db) == ["finding_recorded"]


def test_a_dismissed_finding_stays_dismissed(db: Database) -> None:
    with db.session() as session:
        (recorded,) = record_findings(session, [draft()])
    with db.session() as session:
        dismiss_finding(session, recorded.finding_id, note="not worth it", actor="cli")
    with db.session() as session:
        (again,) = record_findings(session, [draft()])
    assert again.status is FindingStatus.DISMISSED
    assert rows(db)[0].decision_note == "not worth it"
    with db.session() as session, pytest.raises(ValueError, match="already dismissed"):
        dismiss_finding(session, recorded.finding_id)
    with db.session() as session, pytest.raises(LookupError):
        dismiss_finding(session, 9999)


def test_stale_findings_come_back_when_seen_again(db: Database) -> None:
    with db.session() as session:
        record_findings(session, [draft("a"), draft("b")])
    keep = draft("a").fingerprint
    with db.session() as session:
        stale = mark_stale(session, FindingSource.DIAGNOSTIC, [keep])
    assert len(stale) == 1
    assert [r.status for r in rows(db)] == [FindingStatus.NEW, FindingStatus.STALE]
    with db.session() as session:
        (back,) = record_findings(session, [draft("b")])
    assert back.status is FindingStatus.NEW


def test_mark_stale_only_touches_its_own_source(db: Database) -> None:
    roadmap = draft(
        "r",
        source=FindingSource.ROADMAP,
        fingerprint=finding_fingerprint(FindingSource.ROADMAP, "item"),
    )
    with db.session() as session:
        record_findings(session, [roadmap])
        assert mark_stale(session, FindingSource.DIAGNOSTIC, []) == []
    assert rows(db)[0].status is FindingStatus.NEW


def test_stored_text_is_redacted_and_capped(db: Database) -> None:
    secret = "gh" + "p_" + "x" * 36
    with db.session() as session:
        record_findings(session, [draft(title="t" * 500, problem=f"token {secret} leaked")])
    (row,) = rows(db)
    assert len(row.title) == 200
    assert secret not in row.problem
