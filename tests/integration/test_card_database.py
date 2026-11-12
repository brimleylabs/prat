"""A card's code and data land together, or neither does.

Runs when PRAT_TEST_DOLT_URL points at a Dolt server: each test makes its own
project database there.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text

from conftest import PY, TEST_DOLT_URL, add_card, dolt_admin, git, make_engine, project_toml
from prat import store
from prat.config import Settings
from prat.db import Database, Project
from prat.domain import CardStatus, StepName
from prat.engine import ApproveError, FakeReviewer
from prat.workspace import DoltBranchProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TEST_DOLT_URL, reason="set PRAT_TEST_DOLT_URL to a Dolt server"),
]

DB_TOML = '\n[[workspace.database]]\nurl_env = "APP_DB_URL"\ndatabase_env = "DB_DATABASE"\n'


def sql(url: str, *statements: str) -> list[Any]:
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    rows: list[Any] = []
    try:
        with engine.connect() as connection:
            for statement in statements:
                result = connection.execute(text(statement))
                if result.returns_rows:
                    rows.extend(result.all())
    finally:
        engine.dispose()
    return rows


def update(statement: str) -> dict[str, str]:
    """A builder step that changes the database through the project's own setting."""
    code = (
        "import os, sqlalchemy as s; "
        "e = s.create_engine(os.environ['APP_DB_URL'], isolation_level='AUTOCOMMIT'); "
        f"c = e.connect(); c.execute(s.text({statement!r})); c.close()"
    )
    return {"op": "bash", "command": f'{PY} -c "{code}"'}


@pytest.fixture
def appdb(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    name = f"app_{uuid.uuid4().hex[:10]}"
    dolt_admin(f"CREATE DATABASE `{name}`")
    url = f"{TEST_DOLT_URL.rstrip('/')}/{name}"
    sql(
        url,
        "CREATE TABLE products (id INT PRIMARY KEY, name VARCHAR(80), price INT)",
        "INSERT INTO products VALUES (1, 'tent', 50), (2, 'stove', 30)",
        "CALL DOLT_COMMIT('-A', '-m', 'seed')",
    )
    monkeypatch.setenv("APP_DB_URL", url)
    yield url
    dolt_admin(f"DROP DATABASE IF EXISTS `{name}`")


@pytest.fixture
def project(project_factory: Callable[..., Project], appdb: str) -> Project:
    return project_factory(project_toml=project_toml(extra=DB_TOML))


def detail(db: Database, card_id: int, step: StepName) -> dict[str, Any]:
    with db.session() as session:
        run = store.latest_run(session, store.get_card(session, card_id))
        assert run is not None
        return dict(next(s for s in run.steps if s.name == step).detail)


def price(url: str, product: int = 1) -> int:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            value = connection.execute(
                text("SELECT price FROM products WHERE id = :id"), {"id": product}
            ).scalar()
    finally:
        engine.dispose()
    return int(value)


def provider(url: str) -> DoltBranchProvider:
    from prat.projects import DatabaseWorkspace

    return DoltBranchProvider.from_spec(
        DatabaseWorkspace(url_env="APP_DB_URL"), {"APP_DB_URL": url}
    )


async def test_code_and_data_are_reviewed_and_land_together(
    settings: Settings, db: Database, project: Project, appdb: str
) -> None:
    script = [
        {"op": "write", "path": "pricing.py", "content": "OUTDOOR_MARKUP = 1.3\n"},
        update("UPDATE products SET price = 65 WHERE id = 1"),
        {"op": "finish", "message": "repriced"},
    ]
    reviewer = FakeReviewer()
    engine, _ = make_engine(settings, db, script, reviewer=reviewer)
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW, result.reason

    verify = detail(db, card_id, StepName.VERIFY)
    (database,) = verify["databases"]
    (products,) = database["tables"]
    assert products["table"] == "products" and products["rows_modified"] == 1
    assert "data" in verify["changes"]["categories"]
    (request,) = reviewer.requests
    assert request.databases and request.databases[0]["tables"][0]["table"] == "products"
    # Nothing has landed yet: the change lives on the card's branch.
    assert price(appdb) == 50

    merged = await engine.approve(card_id)
    assert price(appdb) == 65
    agent = Path(project.agent_path)
    assert git(agent, "show", "main:pricing.py").startswith("OUTDOOR_MARKUP")
    with db.session() as session:
        events = store.events_for_card(session, store.get_card(session, card_id))
        event = next(e for e in events if e.kind == "card.merged")
    (data_merge,) = event.data["databases"]
    assert data_merge["merge"] == provider(appdb).default_head()
    assert event.data["commit"] == merged.commit


async def test_a_data_only_card_is_real_work(
    settings: Settings, db: Database, project: Project, appdb: str
) -> None:
    script = [update("UPDATE products SET price = 31 WHERE id = 2"), {"op": "finish"}]
    engine, _ = make_engine(settings, db, script)
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW, result.reason
    await engine.approve(card_id)
    assert price(appdb, 2) == 31


async def test_a_data_conflict_lands_nothing(
    settings: Settings, db: Database, project: Project, appdb: str
) -> None:
    script = [
        {"op": "write", "path": "a.py", "content": "x = 1\n"},
        update("UPDATE products SET price = 70 WHERE id = 1"),
        {"op": "finish"},
    ]
    engine, _ = make_engine(settings, db, script)
    card_id = add_card(db, project)
    await engine.run_card(card_id)
    sql(
        appdb,
        "UPDATE products SET price = 80 WHERE id = 1",
        "CALL DOLT_COMMIT('-A', '-m', 'someone else')",
    )
    agent = Path(project.agent_path)
    code_before = git(agent, "rev-parse", "main")
    with pytest.raises(ApproveError, match="database merge failed"):
        await engine.approve(card_id)
    assert price(appdb) == 80
    assert git(agent, "rev-parse", "main") == code_before
    with db.session() as session:
        assert store.get_card(session, card_id).status is CardStatus.REVIEW


async def test_a_code_conflict_undoes_the_data_merge(
    settings: Settings, db: Database, project_factory: Callable[..., Project], appdb: str
) -> None:
    project = project_factory(
        project_toml=project_toml(extra=DB_TOML), files={"app.py": "print('v1')\n"}
    )
    script = [
        {"op": "write", "path": "app.py", "content": "print('card')\n"},
        update("UPDATE products SET price = 90 WHERE id = 1"),
        {"op": "finish"},
    ]
    engine, _ = make_engine(settings, db, script)
    card_id = add_card(db, project)
    await engine.run_card(card_id)
    agent = Path(project.agent_path)
    (agent / "app.py").write_text("print('main moved on')\n", encoding="utf-8")
    git(agent, "commit", "-q", "-am", "concurrent change")
    data_before = provider(appdb).default_head()
    with pytest.raises(ApproveError, match="conflicts"):
        await engine.approve(card_id)
    assert provider(appdb).default_head() == data_before
    assert price(appdb) == 50


async def test_approval_is_bound_to_the_verified_data(
    settings: Settings, db: Database, project: Project, appdb: str
) -> None:
    script = [update("UPDATE products SET price = 55 WHERE id = 1"), {"op": "finish"}]
    engine, _ = make_engine(settings, db, script)
    card_id = add_card(db, project)
    await engine.run_card(card_id)
    branch = provider(appdb).agent_env(card_id)["APP_DB_URL"]
    sql(
        branch,
        "UPDATE products SET price = 1 WHERE id = 2",
        "CALL DOLT_COMMIT('-A', '-m', 'sneaky')",
    )
    with pytest.raises(ApproveError, match="moved since it was verified"):
        await engine.approve(card_id)
    assert price(appdb) == 50


async def test_cancel_drops_the_database_branch(
    settings: Settings, db: Database, project: Project, appdb: str
) -> None:
    script = [update("UPDATE products SET price = 5 WHERE id = 1"), {"op": "finish"}]
    engine, _ = make_engine(settings, db, script)
    card_id = add_card(db, project)
    await engine.run_card(card_id)
    assert provider(appdb).exists(card_id)
    assert await engine.cancel(card_id)
    assert not provider(appdb).exists(card_id)
    assert price(appdb) == 50
