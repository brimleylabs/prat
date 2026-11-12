"""The self-improvement cycle, the scheduler and the `prat improve` commands."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from conftest import WRITE_GREET, git, make_engine, make_repo, project_toml
from prat import store
from prat.cli import app
from prat.config import Settings
from prat.db import Card, Database, ErrorLog, Project
from prat.db.models import ImproveCycle, ImproveEvent, ImproveFinding
from prat.domain import CardStatus, CycleStatus, CycleTrigger, FindingSource, FindingStatus
from prat.errors import record_error
from prat.improve import cycle as cycle_module
from prat.improve.cycle import STANDARD_CRITERIA, fail_interrupted_cycles, run_cycle
from prat.improve.scheduler import ImproveScheduler, may_start, runnable_self_cards
from prat.improve.selfproject import get_self_project, self_repo_path, setup_self_project
from prat.improve.settings import update_improve_settings
from prat.runtime import AgentTask, FakeRuntime, FakeScript

pytestmark = pytest.mark.integration

ROADMAP = """\
# Roadmap

## First planned thing

Effort: low

Do the first thing.

Acceptance criteria:
- the first thing works

## Second planned thing

Do the second thing.

Acceptance criteria:
- the second thing works
"""


def research_answer(*titles: str, **overrides: Any) -> str:
    proposals = [
        {
            "title": title,
            "problem": f"PRAT lacks {title}.",
            "proposal": f"Build {title}.",
            "acceptance_criteria": [f"{title} exists"],
            "sources": ["https://example.org/practice"],
            "effort": "low",
            "severity": "medium",
            **overrides,
        }
        for title in titles
    ]
    return json.dumps({"proposals": proposals, "notes": []})


def researcher(*steps: dict[str, Any]) -> Callable[[AgentTask], FakeScript]:
    """Scripted researcher; any other role gets the greet builder script."""

    def script(task: AgentTask) -> FakeScript:
        if task.metadata.get("role") == "researcher":
            return FakeScript.parse(list(steps))
        return FakeScript.parse(WRITE_GREET)

    return script


@pytest.fixture
def source(tmp_path: Path) -> Path:
    repo = make_repo(
        tmp_path / "src",
        "prat",
        project_toml=project_toml().replace('"acme-api"', '"prat"'),
        files={
            "src/prat/__init__.py": '"""PRAT."""\n',
            "docs/ROADMAP.md": ROADMAP,
            "docs/ARCHITECTURE.md": "# PRAT architecture\n",
        },
    )
    bare = tmp_path / "remote" / "prat.git"
    bare.parent.mkdir(parents=True)
    git(bare.parent, "clone", "-q", "--bare", str(repo), str(bare))
    git(repo, "remote", "add", "origin", str(bare))
    return repo


@pytest.fixture
def ready(settings: Settings, db: Database, source: Path) -> Path:
    """Self-improvement set up and switched on."""
    setup_self_project(db, settings, running_from=source / "src/prat/__init__.py")
    with db.session() as session:
        update_improve_settings(session, enabled=True)
    return source


def configure(db: Database, **changes: Any) -> None:
    with db.session() as session:
        update_improve_settings(session, **changes)


def findings(db: Database) -> list[ImproveFinding]:
    with db.session() as session:
        rows = list(session.scalars(select(ImproveFinding).order_by(ImproveFinding.id)))
        for row in rows:
            session.expunge(row)
        return rows


def events(db: Database) -> list[str]:
    with db.session() as session:
        return list(session.scalars(select(ImproveEvent.kind).order_by(ImproveEvent.id)))


def cycles(db: Database) -> list[ImproveCycle]:
    with db.session() as session:
        rows = list(session.scalars(select(ImproveCycle).order_by(ImproveCycle.id)))
        for row in rows:
            session.expunge(row)
        return rows


def repeated_error(db: Database) -> None:
    for _ in range(3):
        record_error(db, "engine", "OSError", "disk fell over")


# Off, not set up, one at a time ---------------------------------------------------------


async def test_nothing_runs_while_off(settings: Settings, db: Database, source: Path) -> None:
    setup_self_project(db, settings, running_from=source / "src/prat/__init__.py")
    runtime = FakeRuntime(researcher({"op": "finish", "message": research_answer()}))
    result = await run_cycle(db, settings, runtime)
    assert result.status is CycleStatus.SKIPPED
    assert result.message == "self-improvement is off"
    assert cycles(db) == []
    assert runtime.sessions == []
    assert may_start(db).reason == "self-improvement is off"


async def test_nothing_runs_before_setup(settings: Settings, db: Database) -> None:
    configure(db, enabled=True)
    result = await run_cycle(db, settings, FakeRuntime([]))
    assert result.message == "self-improvement is not set up"
    assert not may_start(db).start


async def test_one_cycle_at_a_time(settings: Settings, db: Database, ready: Path) -> None:
    with db.session() as session:
        session.add(ImproveCycle(trigger=CycleTrigger.SCHEDULE, status=CycleStatus.RUNNING))
    result = await run_cycle(db, settings, FakeRuntime([]))
    assert result.status is CycleStatus.SKIPPED and "still running" in result.message
    assert may_start(db).reason == "a cycle is running"
    assert fail_interrupted_cycles(db) == [1]
    (cycle,) = cycles(db)
    assert cycle.status is CycleStatus.FAILED and cycle.error == "server restarted"


# A full cycle ------------------------------------------------------------------------


async def test_a_cycle_files_the_best_findings_as_cards(
    settings: Settings, db: Database, ready: Path
) -> None:
    repeated_error(db)
    runtime = FakeRuntime(
        researcher(
            {"op": "web", "tool": "web_search", "arguments": {"query": "agent practice"}},
            {"op": "cost", "usd": 0.25},
            {"op": "finish", "message": research_answer("Span export", "Skill files")},
        )
    )
    result = await run_cycle(db, settings, runtime)
    assert result.status is CycleStatus.SUCCEEDED, result.message
    assert result.message == "filed 2 card(s)"

    by_source: dict[FindingSource, list[ImproveFinding]] = {}
    for row in findings(db):
        by_source.setdefault(row.source, []).append(row)
    assert len(by_source[FindingSource.DIAGNOSTIC]) == 1
    assert [r.title for r in by_source[FindingSource.ROADMAP]] == [
        "First planned thing",
        "Second planned thing",
    ]
    assert len(by_source[FindingSource.RESEARCH]) == 2
    # Repeated errors first, then the roadmap in file order.
    carded = [r for r in findings(db) if r.status is FindingStatus.CARDED]
    assert [r.title for r in carded] == ["Repeated engine error: OSError", "First planned thing"]

    with db.session() as session:
        cards = list(session.scalars(select(Card).order_by(Card.id)))
        assert [c.id for c in cards] == result.cards
        card = cards[1]
        assert card.status is CardStatus.READY
        assert card.project.is_self
        assert card.budget_usd == 2.0
        assert str(card.complexity) == "low"
        assert card.acceptance_criteria.splitlines() == [
            "- the first thing works",
            *(f"- {line}" for line in STANDARD_CRITERIA),
        ]
        (comment,) = card.comments
        assert comment.kind == "evidence" and "roadmap finding" in comment.body

    (cycle,) = cycles(db)
    assert cycle.status is CycleStatus.SUCCEEDED
    assert cycle.cost_usd == pytest.approx(0.25)
    assert cycle.summary["research"]["proposals"] == 2
    assert cycle.summary["health"][0]["name"] == "database"
    everything = events(db)
    kinds = everything[everything.index("cycle_started") :]
    assert everything[:2] == ["setup", "toggled"]
    assert kinds[-1] == "cycle_finished"
    assert "sync_ok" in kinds and kinds.count("card_filed") == 2


async def test_research_off(settings: Settings, db: Database, ready: Path) -> None:
    configure(db, research_enabled=False)
    runtime = FakeRuntime(researcher({"op": "finish", "message": research_answer("x")}))
    result = await run_cycle(db, settings, runtime)
    assert result.summary["research"] == "off"
    assert runtime.sessions == []


async def test_the_open_card_cap_holds(settings: Settings, db: Database, ready: Path) -> None:
    configure(db, max_open_cards=1, research_enabled=False)
    first = await run_cycle(db, settings, FakeRuntime([]))
    assert len(first.cards) == 1
    with db.session() as session:
        cycle = session.get(ImproveCycle, first.cycle_id)
        assert cycle is not None
        cycle.started_at = cycle.started_at - timedelta(days=1)
    second = await run_cycle(db, settings, FakeRuntime([]))
    assert second.cards == []
    assert "1 self cards are open (cap 1)" in second.message


async def test_the_daily_budget_holds(settings: Settings, db: Database, ready: Path) -> None:
    configure(db, daily_budget_usd=0)
    runtime = FakeRuntime(researcher({"op": "finish", "message": research_answer("x")}))
    result = await run_cycle(db, settings, runtime)
    assert result.cards == []
    assert "daily budget" in result.message
    assert result.summary["research"] == "skipped: the daily budget is spent"
    assert runtime.sessions == []
    assert "daily budget is spent" in may_start(db).reason or not may_start(db).start


async def test_turning_off_during_research_cancels_the_cycle(
    settings: Settings, db: Database, ready: Path
) -> None:
    def script(task: AgentTask) -> FakeScript:
        configure(db, enabled=False)  # a person turns it off while research runs
        return FakeScript.parse(
            [{"op": "text", "text": "searching"}, {"op": "wait", "seconds": 30}]
        )

    result = await run_cycle(db, settings, FakeRuntime(script))
    assert result.status is CycleStatus.CANCELLED
    assert result.cards == []
    assert cycles(db)[0].status is CycleStatus.CANCELLED
    assert "cycle_cancelled" in events(db)


async def test_screened_proposals_are_dismissed(
    settings: Settings, db: Database, ready: Path
) -> None:
    configure(db, max_new_cards_per_cycle=1)
    answer = research_answer(
        "Faster landings", proposal="Skip verification gates for small changes."
    )
    await run_cycle(db, settings, FakeRuntime(researcher({"op": "finish", "message": answer})))
    (row,) = [r for r in findings(db) if r.source is FindingSource.RESEARCH]
    assert row.status is FindingStatus.DISMISSED
    assert row.decision_note == "screened out: asks to weaken a protection"
    assert "finding_dismissed" in events(db)


async def test_a_held_finding_can_be_let_through_for_the_text_seen(
    settings: Settings, db: Database, ready: Path
) -> None:
    from prat.improve.findings import allow_screened, is_screened

    configure(db, max_new_cards_per_cycle=1, max_open_cards=5, interval_minutes=5)
    held = research_answer(
        "Repair loop", proposal="Retry once; the final verify must pass with no gate skipped."
    )
    agent = FakeRuntime(researcher({"op": "finish", "message": held}))
    await run_cycle(db, settings, agent)
    (row,) = [r for r in findings(db) if r.source is FindingSource.RESEARCH]
    assert is_screened(row)
    with pytest.raises(ValueError, match="not held back"), db.session() as session:
        allow_screened(session, row.id - 1 if row.id > 1 else row.id + 100, actor="cli")
    with db.session() as session:
        allow_screened(session, row.id, actor="cli")

    # Proposed again with the same text: it stays in the queue.
    await run_cycle(db, settings, agent)
    (again,) = [r for r in findings(db) if r.source is FindingSource.RESEARCH]
    assert again.status is FindingStatus.NEW
    assert again.decision_note.startswith("let through by cli")

    # Proposed with new wording a person has not seen: held again.
    reworded = research_answer(
        "Repair loop", proposal="Retry, and skip the security gate when it is slow."
    )
    await run_cycle(db, settings, FakeRuntime(researcher({"op": "finish", "message": reworded})))
    (last,) = [r for r in findings(db) if r.source is FindingSource.RESEARCH]
    assert is_screened(last)


async def test_rejected_research_output_is_recorded_and_the_cycle_goes_on(
    settings: Settings, db: Database, ready: Path
) -> None:
    runtime = FakeRuntime(researcher({"op": "finish", "message": "no JSON here"}))
    result = await run_cycle(db, settings, runtime)
    assert result.status is CycleStatus.SUCCEEDED
    assert len(result.cards) == 2  # the roadmap still feeds the cycle
    assert "research_rejected" in events(db)
    with db.session() as session:
        logged = session.scalars(select(ErrorLog).where(ErrorLog.source == "improve")).all()
        assert [row.kind for row in logged] == ["research_rejected"]


async def test_a_diverged_remote_blocks_filing(
    settings: Settings, db: Database, ready: Path, tmp_path: Path
) -> None:
    clone = self_repo_path(settings)
    (clone / "local.txt").write_text("x", encoding="utf-8")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "local only")
    pusher = tmp_path / "pusher"
    git(tmp_path, "clone", "-q", git(ready, "config", "remote.origin.url"), str(pusher))
    git(pusher, "config", "user.name", "T")
    git(pusher, "config", "user.email", "t@example.invalid")
    (pusher / "remote.txt").write_text("y", encoding="utf-8")
    git(pusher, "add", "-A")
    git(pusher, "commit", "-q", "-m", "remote only")
    git(pusher, "push", "-q", "origin", "main")

    configure(db, research_enabled=False)
    result = await run_cycle(db, settings, FakeRuntime([]))
    assert result.cards == []
    assert "diverged" in result.message
    health = [r for r in findings(db) if r.title == "Health check failing: self_sync"]
    assert health and health[0].severity.value == "high"


async def test_a_crash_fails_the_cycle_cleanly(
    settings: Settings, db: Database, ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("diagnostics broke")

    monkeypatch.setattr(cycle_module, "diagnose", broken)
    result = await run_cycle(db, settings, FakeRuntime([]))
    assert result.status is CycleStatus.FAILED
    (cycle,) = cycles(db)
    assert cycle.status is CycleStatus.FAILED and "diagnostics broke" in (cycle.error or "")
    assert events(db)[-1] == "cycle_failed"
    with db.session() as session:
        assert session.scalars(select(ErrorLog.kind)).all() == ["RuntimeError"]


# The scheduler -----------------------------------------------------------------------


async def test_the_interval_decides_when_the_next_cycle_is_due(
    settings: Settings, db: Database, ready: Path
) -> None:
    configure(db, research_enabled=False, interval_minutes=60)
    assert may_start(db).start
    await run_cycle(db, settings, FakeRuntime([]))
    assert may_start(db).reason == "the interval since the last cycle has not passed"
    later = datetime.now(UTC) + timedelta(minutes=61)
    assert may_start(db, later).start


async def test_runnable_self_cards_respect_the_rework_limit(
    settings: Settings, db: Database, ready: Path
) -> None:
    configure(db, research_enabled=False, max_new_cards_per_cycle=1, max_reworks=0)
    result = await run_cycle(db, settings, FakeRuntime([]))
    (card_id,) = result.cards
    assert runnable_self_cards(db) == [card_id]
    engine, _ = make_engine(settings, db, FakeRuntime(researcher()))
    await engine.run_card(card_id)
    with db.session() as session:
        card = store.get_card(session, card_id)
        store.set_card_status(session, card, CardStatus.REWORK, "test")
    assert runnable_self_cards(db) == []  # one run used, no reworks allowed
    configure(db, enabled=False)
    assert runnable_self_cards(db) == []


async def test_a_scheduler_tick_runs_a_cycle_and_its_cards(
    settings: Settings, db: Database, ready: Path
) -> None:
    configure(db, max_new_cards_per_cycle=1)
    runtime = FakeRuntime(researcher({"op": "finish", "message": research_answer()}))
    engine, _ = make_engine(settings, db, runtime)
    scheduler = ImproveScheduler(db, settings, engine, tick_seconds=3600)
    await scheduler.tick()
    assert scheduler.cycle_running
    assert scheduler._cycle is not None
    result = await scheduler._cycle
    (card_id,) = result.cards
    for _ in range(200):
        if not engine.is_scheduled(card_id):
            break
        await asyncio.sleep(0.05)
    with db.session() as session:
        assert store.get_card(session, card_id).status is CardStatus.REVIEW
    # A second tick inside the interval starts nothing new.
    await scheduler.tick()
    assert len(cycles(db)) == 1
    await scheduler.stop()


async def test_start_and_stop_recover_interrupted_cycles(
    settings: Settings, db: Database, ready: Path
) -> None:
    with db.session() as session:
        session.add(ImproveCycle(trigger=CycleTrigger.SCHEDULE, status=CycleStatus.RUNNING))
    configure(db, enabled=False)
    engine, _ = make_engine(settings, db, FakeRuntime([]))
    scheduler = ImproveScheduler(db, settings, engine, tick_seconds=3600)
    scheduler.start()
    await asyncio.sleep(0)
    await scheduler.stop()
    assert cycles(db)[0].error == "server restarted"


async def test_run_now_waits_for_the_cycle(settings: Settings, db: Database, ready: Path) -> None:
    configure(db, research_enabled=False)
    engine, _ = make_engine(settings, db, FakeRuntime([]))
    scheduler = ImproveScheduler(db, settings, engine, tick_seconds=3600)
    result = await scheduler.run_now()
    assert result.status is CycleStatus.SUCCEEDED
    assert cycles(db)[0].trigger is CycleTrigger.MANUAL
    await scheduler.stop()


# The CLI -----------------------------------------------------------------------------


def test_the_improve_commands(tmp_path: Path, source: Path) -> None:
    runner = CliRunner()
    home = tmp_path / "home"

    def cli(*args: str, code: int = 0) -> str:
        # Wide enough that table rows are not wrapped.
        result = runner.invoke(app, ["--home", str(home), *args], env={"COLUMNS": "240"})
        assert result.exit_code == code, result.output
        return result.output

    cli("init")
    assert "not set up" in cli("improve", "status")
    out = cli("improve", "on")
    assert "is on" in out and "prat improve setup" in out
    url = git(source, "config", "remote.origin.url")
    assert "self project created" in cli("improve", "setup", "--repo-url", url)
    assert "set to 3" in cli("improve", "set", "daily_budget_usd", "3")
    assert "invalid" in cli("improve", "set", "max_open_cards", "0", code=1)
    assert "unknown" in cli("improve", "set", "autopilot", "true", code=1)
    assert "review" in cli("improve", "mode", "review")
    cli("improve", "mode", "sideways", code=1)
    cli("improve", "set", "research_enabled", "false")

    script = tmp_path / "research.json"
    script.write_text(
        json.dumps([{"op": "finish", "message": research_answer()}]), encoding="utf-8"
    )
    out = cli("improve", "run", "--script", str(script))
    assert "succeeded" in out and "filed card" in out

    status = cli("improve", "status")
    assert "self-improvement: on, mode review" in status
    assert "last cycle: #1 manual succeeded" in status
    listing = cli("improve", "findings", "--source", "roadmap")
    assert "First planned thing" in listing
    finding_id = next(
        line.split()[1]
        for line in listing.splitlines()
        if "Second planned thing" in line and line.split()[1].isdigit()
    )
    assert "dismissed" in cli("improve", "dismiss", finding_id, "--note", "later")
    assert "already dismissed" in cli("improve", "dismiss", finding_id, code=1)
    cli("improve", "findings", "--status", "nonsense", code=1)
    assert "is off" in cli("improve", "off")
    assert "skipped" in cli("improve", "run", "--script", str(script))


def test_the_self_project_is_not_a_normal_project_target(
    settings: Settings, db: Database, ready: Path
) -> None:
    with db.session() as session:
        project = get_self_project(session)
        assert project is not None
        assert project.checkout_path == project.agent_path
        others = session.scalars(select(Project).where(Project.is_self.is_(False))).all()
        assert others == []
