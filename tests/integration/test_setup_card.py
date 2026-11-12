"""The setup agent: a setup card proposes the project file, PRAT proves it and it lands."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from conftest import PY, add_card, git, make_engine
from prat import store
from prat.config import Settings
from prat.db import Card, Database, Project, Run
from prat.domain import Autonomy, CardKind, CardStatus
from prat.environments import load_local, set_variable
from prat.runtime import AgentTask, FakeRuntime, FakeScript
from prat.setup_agent import DATABASE_VARIABLE, add_setup_card, needs_setup

pytestmark = pytest.mark.integration

SERVER = (
    "import http.server, sys\n"
    "port = int(sys.argv[1])\n"
    "http.server.HTTPServer(('127.0.0.1', port), http.server.SimpleHTTPRequestHandler)"
    ".serve_forever()\n"
)


def proposal(*, health: bool = True, test_code: str = "print('tests ok')") -> str:
    health_line = 'health = "http://127.0.0.1:{port}/"' if health else ""
    return f"""
name = "widgets"
default_branch = "main"
autonomy = "medium"
notes = \"\"\"A tiny web app. Run it with the web environment.\"\"\"

[commands]
setup = ['{PY}', '-c', "print('installed')"]
test = ['{PY}', '-c', "{test_code}"]

[verify]
gates = ["setup", "test"]
timeout_seconds = 60

[[environment]]
name = "web"
start = ['{PY}', 'serve.py', '{{port}}']
{health_line}
ready_seconds = 20
"""


def setup_script(toml: str) -> list[dict[str, Any]]:
    return [
        {"op": "write", "path": "serve.py", "content": SERVER},
        {"op": "write", "path": ".prat/project.toml", "content": toml},
        {"op": "write", "path": ".prat/setup.sh", "content": "echo installed\n"},
        {"op": "finish", "message": "Python app; setup, test and a web environment."},
    ]


def run_of(db: Database, card_id: int) -> Run:
    with db.session() as session:
        card = session.get(Card, card_id)
        assert card is not None
        run = card.runs[-1]
        for step in run.steps:
            _ = step.detail
        return run


def gates_of(run: Run) -> list[dict[str, Any]]:
    verify = next(s for s in run.steps if str(s.name) == "verify")
    return list(verify.detail.get("gates") or [])


@pytest.fixture
def bare(project_factory: Callable[..., Project]) -> Project:
    """A project whose repository has no project file yet."""
    return project_factory(name="widgets", project_toml=None)


async def test_the_setup_card_proposes_a_project_file_that_prat_proves(
    settings: Settings, db: Database, bare: Project
) -> None:
    assert needs_setup(Path(bare.checkout_path))
    with db.session() as session:
        card_id = add_setup_card(session, store.get_project(session, bare.id), settings).id
    seen: list[AgentTask] = []

    def script_for(task: AgentTask) -> FakeScript:
        seen.append(task)
        return FakeScript.parse(setup_script(proposal()))

    engine, _ = make_engine(settings, db, FakeRuntime(script_for))
    result = await engine.run_card(card_id)

    assert result.card_status is CardStatus.REVIEW, result
    task = seen[0]
    assert task.metadata["role"] == "setup"
    assert "only agent allowed to write the project's .prat/ folder" in task.system_prompt
    gates = {g["name"]: g for g in gates_of(run_of(db, card_id))}
    assert gates["setup"]["passed"] and gates["test"]["passed"]
    assert gates["environment:web"]["passed"], gates["environment:web"]["output_tail"]

    await engine.approve(card_id)
    agent = Path(bare.agent_path)
    shown = git(agent, "show", "main:.prat/project.toml")
    assert 'name = "widgets"' in shown
    assert git(agent, "show", "main:.prat/setup.sh").strip() == "echo installed"


async def test_a_proposal_that_fails_its_own_checks_does_not_reach_review(
    settings: Settings, db: Database, bare: Project
) -> None:
    with db.session() as session:
        card_id = add_setup_card(session, store.get_project(session, bare.id), settings).id
    script = setup_script(proposal(test_code="import sys; sys.exit(3)"))
    engine, _ = make_engine(settings, db, script)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.FAILED
    gates = {g["name"]: g for g in gates_of(run_of(db, card_id))}
    assert not gates["test"]["passed"]
    assert "environment:web" not in gates  # environments are only tried once the gates pass


async def test_a_missing_project_file_is_a_failed_check(
    settings: Settings, db: Database, bare: Project
) -> None:
    with db.session() as session:
        card_id = add_setup_card(session, store.get_project(session, bare.id), settings).id
    script = [{"op": "write", "path": "notes.txt", "content": "thinking\n"}, {"op": "finish"}]
    engine, _ = make_engine(settings, db, script)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.FAILED
    (gate,) = gates_of(run_of(db, card_id))
    assert gate["name"] == "project-file" and "no .prat/project.toml" in gate["output_tail"]


async def test_an_environment_that_never_answers_fails_the_check(
    settings: Settings, db: Database, bare: Project
) -> None:
    with db.session() as session:
        card_id = add_setup_card(session, store.get_project(session, bare.id), settings).id
    broken = setup_script(proposal())
    broken[0] = {"op": "write", "path": "serve.py", "content": "import sys; sys.exit(1)\n"}
    engine, _ = make_engine(settings, db, broken)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.FAILED
    gates = {g["name"]: g for g in gates_of(run_of(db, card_id))}
    assert not gates["environment:web"]["passed"]


async def test_only_the_setup_card_may_write_the_project_file(
    settings: Settings, db: Database, bare: Project
) -> None:
    card_id = add_card(db, bare, title="Add a feature")
    with db.session() as session:
        assert session.get(Card, card_id).kind is CardKind.CHANGE  # type: ignore[union-attr]
    script = [
        {"op": "write", "path": ".prat/project.toml", "content": proposal()},
        {"op": "write", "path": "feature.txt", "content": "x\n"},
        {"op": "finish"},
    ]
    engine, _ = make_engine(settings, db, script)
    await engine.run_card(card_id)
    worktree_files = git(
        Path(bare.agent_path), "ls-tree", "-r", "--name-only", f"card/{card_id}-add-a-feature"
    )
    assert ".prat/project.toml" not in worktree_files


def test_the_setup_agent_is_given_the_project_database(
    settings: Settings, db: Database, bare: Project
) -> None:
    set_variable(
        settings.home,
        bare.slug,
        DATABASE_VARIABLE,
        "postgresql+psycopg://u:p@127.0.0.1:5480/widgets",
    )
    set_variable(settings.home, bare.slug, "OTHER", "x")
    set_variable(
        settings.home,
        bare.slug,
        DATABASE_VARIABLE,
        "postgresql+psycopg://u:p@127.0.0.1:5480/widgets2",
    )
    local = load_local(settings.home, bare.slug)
    assert local.problem is None
    assert local.variables[DATABASE_VARIABLE] == "postgresql+psycopg://u:p@127.0.0.1:5480/widgets2"
    assert local.variables["OTHER"] == "x"


async def test_a_setup_card_runs_at_medium_whatever_the_projects_default(
    settings: Settings, db: Database, bare: Project
) -> None:
    with db.session() as session:
        card_id = add_setup_card(session, store.get_project(session, bare.id), settings).id
    engine, _ = make_engine(settings, db, setup_script(proposal()))
    await engine.run_card(card_id)
    assert str(run_of(db, card_id).autonomy) == "medium"
    # The installation's ceiling still wins.
    with db.session() as session:
        second = add_setup_card(session, store.get_project(session, bare.id), settings).id
    low_engine, _ = make_engine(settings, db, setup_script(proposal()), max_autonomy=Autonomy.LOW)
    await low_engine.run_card(second)
    assert str(run_of(db, second).autonomy) == "low"


def test_checkout_variables_follow_the_card_branch_rules(settings: Settings, bare: Project) -> None:
    from prat.environments import checkout_variables
    from prat.projects import DatabaseWorkspace

    set_variable(
        settings.home, bare.slug, DATABASE_VARIABLE, "postgresql+psycopg://u:p@127.0.0.1:5480/shop"
    )
    database = DatabaseWorkspace(
        name="app",
        kind="doltgres",
        url_env=DATABASE_VARIABLE,
        database_env="DB_NAME",
        app_url_env="DATABASE_URL",
    )
    variables = checkout_variables(settings.home, bare.slug, [database])
    assert variables["DB_NAME"] == "shop"
    assert variables["DATABASE_URL"] == "postgresql://u:p@127.0.0.1:5480/shop"
    assert variables[DATABASE_VARIABLE].startswith("postgresql+psycopg://")
