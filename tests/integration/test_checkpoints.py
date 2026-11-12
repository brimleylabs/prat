"""Checkpoints: undo one card's code and data, or rewind both to a landed card."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from typer.testing import CliRunner

from conftest import (
    DEFAULT_TOML,
    PY,
    TEST_DOLT_URL,
    WRITE_GREET,
    add_card,
    dolt_admin,
    git,
    make_engine,
    project_toml,
)
from prat.cli import app
from prat.config import Settings
from prat.db import Database, Project
from prat.workspace.checkpoints import CheckpointError, list_checkpoints, rewind_to, undo_card

pytestmark = pytest.mark.integration
needs_dolt = pytest.mark.skipif(not TEST_DOLT_URL, reason="set PRAT_TEST_DOLT_URL to a Dolt server")

DB_TOML = '\n[[workspace.database]]\nurl_env = "APP_DB_URL"\n'


def sql(url: str, *statements: str) -> None:
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            for statement in statements:
                connection.execute(text(statement))
    finally:
        engine.dispose()


def prices(url: str) -> dict[int, int]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT id, price FROM products")).all()
    finally:
        engine.dispose()
    return {int(r[0]): int(r[1]) for r in rows}


def card_script(path: str, statement: str | None = None) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = [{"op": "write", "path": path, "content": f"# {path}\n"}]
    if statement:
        code = (
            "import os, sqlalchemy as s; "
            "e = s.create_engine(os.environ['APP_DB_URL'], isolation_level='AUTOCOMMIT'); "
            f"c = e.connect(); c.execute(s.text({statement!r})); c.close()"
        )
        steps.append({"op": "bash", "command": f'{PY} -c "{code}"'})
    steps.append({"op": "finish"})
    return steps


async def land(
    settings: Settings, db: Database, project: Project, script: list[Any], title: str
) -> int:
    engine, _ = make_engine(settings, db, script)
    card_id = add_card(db, project, title)
    result = await engine.run_card(card_id)
    assert result.card_status.value == "review", result.reason
    await engine.approve(card_id)
    return card_id


def files_on_main(project: Project) -> set[str]:
    return set(git(Path(project.agent_path), "ls-tree", "--name-only", "main").split())


# Code only --------------------------------------------------------------------------


async def test_undo_a_code_only_card(settings: Settings, db: Database, project: Project) -> None:
    card_id = await land(settings, db, project, WRITE_GREET, "greet")
    (checkpoint,) = list_checkpoints(db)
    assert checkpoint.card_id == card_id and not checkpoint.undone
    assert "hello.py" in files_on_main(project)

    result = undo_card(db, card_id)
    assert "hello.py" not in files_on_main(project)
    assert git(Path(project.agent_path), "rev-parse", "main") == result.git_revert
    assert list_checkpoints(db)[0].undone
    with pytest.raises(CheckpointError, match="already been undone"):
        undo_card(db, card_id)


def test_no_checkpoint_before_landing(db: Database, project: Project) -> None:
    card_id = add_card(db, project)
    with pytest.raises(CheckpointError, match="has not landed"):
        undo_card(db, card_id)


def test_the_checkpoint_commands(tmp_path: Path, repo_factory: Callable[..., Path]) -> None:
    import json

    runner = CliRunner()
    home = tmp_path / "cli-home"

    def cli(*args: str, code: int = 0) -> str:
        result = runner.invoke(app, ["--home", str(home), *args], env={"COLUMNS": "200"})
        assert result.exit_code == code, result.output
        return result.output

    repo = repo_factory("cli-app", project_toml=DEFAULT_TOML.replace('"acme-api"', '"cli-app"'))
    script = tmp_path / "script.json"
    script.write_text(json.dumps(WRITE_GREET), encoding="utf-8")
    cli("init")
    cli("project", "add", str(repo))
    cli("card", "add", "cli-app", "Add greet")
    cli("card", "run", "1", "--script", str(script))
    cli("card", "approve", "1")
    assert "landed" in cli("checkpoint", "list")
    assert "drops everything" in cli("checkpoint", "rewind", "1", code=1)
    assert "card 1 undone" in cli("checkpoint", "undo", "1")
    assert "undone" in cli("checkpoint", "list")


# Code and data ----------------------------------------------------------------------


@pytest.fixture
def appdb(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    name = f"app_{uuid.uuid4().hex[:10]}"
    dolt_admin(f"CREATE DATABASE `{name}`")
    url = f"{TEST_DOLT_URL.rstrip('/')}/{name}"
    sql(
        url,
        "CREATE TABLE products (id INT PRIMARY KEY, price INT)",
        "INSERT INTO products VALUES (1, 50), (2, 30)",
        "CALL DOLT_COMMIT('-A', '-m', 'seed')",
    )
    monkeypatch.setenv("APP_DB_URL", url)
    yield url
    dolt_admin(f"DROP DATABASE IF EXISTS `{name}`")


@pytest.fixture
def data_project(project_factory: Callable[..., Project], appdb: str) -> Project:
    return project_factory("shop", project_toml=project_toml(extra=DB_TOML))


@needs_dolt
async def test_undo_one_card_keeps_the_later_one(
    settings: Settings, db: Database, data_project: Project, appdb: str
) -> None:
    first = await land(
        settings,
        db,
        data_project,
        card_script("a.py", "UPDATE products SET price = 65 WHERE id = 1"),
        "a",
    )
    await land(
        settings,
        db,
        data_project,
        card_script("b.py", "UPDATE products SET price = 35 WHERE id = 2"),
        "b",
    )
    assert prices(appdb) == {1: 65, 2: 35}
    result = undo_card(db, first)
    assert prices(appdb) == {1: 50, 2: 35}
    files = files_on_main(data_project)
    assert "a.py" not in files and "b.py" in files
    assert len(result.databases) == 1


@needs_dolt
async def test_an_undo_that_conflicts_changes_nothing(
    settings: Settings, db: Database, data_project: Project, appdb: str
) -> None:
    first = await land(
        settings,
        db,
        data_project,
        card_script("a.py", "UPDATE products SET price = 65 WHERE id = 1"),
        "a",
    )
    await land(
        settings,
        db,
        data_project,
        card_script("b.py", "UPDATE products SET price = 70 WHERE id = 1"),
        "b",
    )
    code_before = git(Path(data_project.agent_path), "rev-parse", "main")
    with pytest.raises(CheckpointError, match="could not be reverted"):
        undo_card(db, first)
    assert prices(appdb) == {1: 70, 2: 30}
    assert git(Path(data_project.agent_path), "rev-parse", "main") == code_before
    assert not list_checkpoints(db)[-1].undone


@needs_dolt
async def test_rewind_puts_code_and_data_back_together(
    settings: Settings, db: Database, data_project: Project, appdb: str
) -> None:
    first = await land(
        settings,
        db,
        data_project,
        card_script("a.py", "UPDATE products SET price = 65 WHERE id = 1"),
        "a",
    )
    await land(
        settings,
        db,
        data_project,
        card_script("b.py", "UPDATE products SET price = 35 WHERE id = 2"),
        "b",
    )
    with pytest.raises(CheckpointError, match="drops everything"):
        rewind_to(db, first)
    result = rewind_to(db, first, confirm=True)
    assert prices(appdb) == {1: 65, 2: 30}
    files = files_on_main(data_project)
    assert "a.py" in files and "b.py" not in files
    agent = Path(data_project.agent_path)
    assert result.backup_tag in git(agent, "tag")
    engine = create_engine(appdb)
    try:
        with engine.connect() as connection:
            tags = [r[0] for r in connection.execute(text("SELECT tag_name FROM dolt_tags"))]
    finally:
        engine.dispose()
    assert result.backup_tag in tags
