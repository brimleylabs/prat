from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from conftest import DEFAULT_TOML, WRITE_GREET, add_card, git, make_engine
from prat import store
from prat.cli import app
from prat.config import Settings
from prat.db import Database, Project, Run
from prat.domain import CardStatus
from prat.evals import EvalOptions, builtin_suite, load_case, load_cases, run_case, run_suite
from prat.evals.export import export_card

pytestmark = pytest.mark.integration

EXPECTED = {
    "chunks": ("stopped", ["test failed", "test failed"]),
    "clamp": ("kept", ["not_met", "met"]),
    "duration": ("fixed", ["not_met", "met"]),
    "greet": ("kept", ["met"]),
    "roman": ("missed", ["met"]),
    "slugify": ("fixed", ["test failed", "met"]),
}


async def test_the_builtin_suite_scores_every_outcome(settings: Settings) -> None:
    cases = load_cases(builtin_suite())
    result = await run_suite(
        settings, cases, EvalOptions(keep_work=True, overlay=False), suite="built-in"
    )
    got = {
        case.name: (case.outcome, [attempt.trail() for attempt in case.attempts])
        for case in result.cases
    }
    assert got == EXPECTED, [case.error for case in result.cases]
    calls = [attempt.call for case in result.cases for attempt in case.attempts]
    assert calls.count("caught") == 4
    assert calls.count("missed") == 1
    assert calls.count("false_alarm") == 1
    assert calls.count("passed") == 4

    folder = settings.home / "evals" / result.id
    assert (folder / "result.json").is_file()
    assert (folder / "journals" / "slugify" / "attempt-2.jsonl").is_file()

    # Each rework run was told why: the gate output, or the reviewer's fix list.
    def feedback(case: str) -> str:
        db = Database(f"sqlite:///{(folder / 'work' / case / 'home' / 'prat.db').as_posix()}")
        try:
            with db.session() as session:
                runs = list(session.scalars(select(Run).order_by(Run.id)))
                return runs[1].feedback or ""
        finally:
            db.dispose()

    assert "Output of test:" in feedback("slugify")
    assert "hello-world-" in feedback("slugify")
    assert "must raise ValueError" in feedback("duration")


async def test_cases_never_touch_the_installation(settings: Settings, db: Database) -> None:
    (greet,) = load_cases(builtin_suite(), ["greet"])
    result = await run_case(settings, greet, EvalOptions(), settings.home / "work" / "greet")
    assert result.outcome == "kept", result.error
    with db.session() as session:
        assert list(session.scalars(select(Project))) == []


async def test_a_case_declaring_databases_is_refused(settings: Settings, tmp_path: Path) -> None:
    case_dir = tmp_path / "with-db"
    (case_dir / "repo" / ".prat").mkdir(parents=True)
    (case_dir / "repo" / ".prat" / "project.toml").write_text(
        'name = "with-db"\n\n[[workspace.database]]\nname = "main"\nurl_env = "APP_DB"\n',
        encoding="utf-8",
    )
    (case_dir / "case.toml").write_text('title = "T"\n', encoding="utf-8")
    (case_dir / "attempts").mkdir()
    (case_dir / "attempts" / "1.json").write_text('[{"op": "finish"}]', encoding="utf-8")
    result = await run_case(settings, load_case(case_dir), EvalOptions(), tmp_path / "work")
    assert result.outcome == "error"
    assert "databases" in (result.error or "")


async def test_a_finished_card_is_exported_and_replayed(
    settings: Settings, db: Database, project_factory: Callable[..., Project], tmp_path: Path
) -> None:
    project = project_factory(files={"old.txt": "old\n"})
    card_id = add_card(
        db, project, title="Add greet", goal="Add hello.greet()", acceptance_criteria="- greets"
    )
    script = [
        {"op": "bash", "command": "git push"},
        *WRITE_GREET[:-1],
        {"op": "delete", "path": "old.txt"},
        WRITE_GREET[-1],
    ]
    engine, _ = make_engine(settings, db, script)
    outcome = await engine.run_card(card_id)
    assert outcome.card_status == CardStatus.REVIEW
    agent_clone = Path(project.agent_path)
    with db.session() as session:
        branch = store.get_card(session, card_id).branch or ""
    head_before = git(agent_clone, "rev-parse", branch)

    report = export_card(db, settings, card_id, tmp_path / "cases" / "add-greet")
    assert report.attempts == 1
    case = load_case(report.path)
    assert case.spec.title == "Add greet"
    assert case.spec.acceptance_criteria == "- greets"
    assert case.spec.recorded is not None
    (recorded,) = case.spec.recorded.runs
    assert recorded.run_status == "succeeded"
    assert recorded.gates == {"test": True}
    assert recorded.denied == 1
    assert (report.path / "journals" / f"run-{recorded.run_id}.jsonl").is_file()
    assert (report.path / "repo" / "old.txt").is_file()
    steps = json.loads((report.path / "attempts" / "1.json").read_text(encoding="utf-8"))
    ops = [(step["op"], step.get("dry", False)) for step in steps["steps"]]
    assert ("bash", True) in ops
    assert ("write", False) in ops
    assert ("delete", False) in ops

    result = await run_case(settings, case, EvalOptions(), tmp_path / "replay")
    assert result.error is None
    (replayed,) = result.attempts
    assert replayed.card_status == "review"
    assert [gate.name for gate in replayed.gates if gate.passed] == ["test"]
    # Today's policy still denies the recorded push; nothing ran.
    assert replayed.policy.denied == 1
    assert result.recorded == case.spec.recorded.runs

    # The original card and branch are as they were.
    with db.session() as session:
        assert store.get_card(session, card_id).status == CardStatus.REVIEW
    assert git(agent_clone, "rev-parse", branch) == head_before
    with pytest.raises(ValueError, match="not empty"):
        export_card(db, settings, card_id, report.path)


@pytest.fixture
def cli(tmp_path: Path) -> Callable[..., str]:
    runner = CliRunner()
    home = tmp_path / "home"

    def invoke(*args: str, code: int = 0) -> str:
        result = runner.invoke(app, ["--home", str(home), *args])
        assert result.exit_code == code, result.output
        return result.output

    return invoke


def test_eval_commands(
    cli: Callable[..., str], repo_factory: Callable[..., Path], tmp_path: Path
) -> None:
    out = cli("eval", "run", "--case", "greet", "--case", "roman", "--label", "first")
    assert "greet: kept" in out
    assert "roman: missed" in out
    assert "first attempt passes the hidden check" in out
    assert "Scripted builder and reviewer" in out
    cli("eval", "run", "--case", "greet", "--case", "slugify", "--attempts", "1")
    listed = cli("eval", "list")
    assert "first" in listed
    shown = cli("eval", "show")
    assert "attempt 1:" in shown
    compared = cli("eval", "compare")
    assert "review layer" in compared
    assert "left out (not in both): roman, slugify" in compared
    assert "unknown runtime" in cli("eval", "run", "--runtime", "other", code=1)
    assert "no such case" in cli("eval", "run", "--case", "nothing", code=1)

    # Export a finished card and run it.
    repo = repo_factory("acme-api", project_toml=DEFAULT_TOML)
    script = tmp_path / "script.json"
    script.write_text(json.dumps(WRITE_GREET), encoding="utf-8")
    cli("init")
    cli("project", "add", str(repo))
    cli("card", "add", "acme-api", "Add greet", "--ready")
    cli("card", "run", "1", "--script", str(script))
    dest = tmp_path / "add-greet"
    assert "exported card 1" in cli("eval", "export", "1", str(dest))
    replayed = cli("eval", "run", str(dest))
    assert "add-greet: handed_over" in replayed
