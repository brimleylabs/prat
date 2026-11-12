"""A Doltgres branch per card for a project's database, end to end.

Runs when PRAT_TEST_DOLTGRES_URL points at a Doltgres server (for example
``postgresql+psycopg://postgres:password@127.0.0.1:5480``); each test makes its
own project database there and drops it afterwards.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text

from conftest import PY, TEST_DOLTGRES_URL, add_card, doltgres_admin, git, make_engine, project_toml
from prat import store
from prat.config import Settings
from prat.db import Database, Project
from prat.domain import CardStatus, StepName
from prat.engine import ApproveError, FakeReviewer
from prat.projects import DatabaseWorkspace, parse_spec
from prat.workspace import DatabaseMergeConflict, DoltgresBranchProvider, WorkspaceError
from prat.workspace.checkpoints import CheckpointError, rewind_to, undo_card
from prat.workspace.dolt_branch import providers_for

pytestmark = pytest.mark.integration
needs_doltgres = pytest.mark.skipif(
    not TEST_DOLTGRES_URL, reason="set PRAT_TEST_DOLTGRES_URL to a Doltgres server"
)

DB_TOML = (
    '\n[[workspace.database]]\nkind = "doltgres"\nurl_env = "APP_DB_URL"\n'
    'database_env = "DB_DATABASE"\n'
)


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


def prices(url: str) -> dict[int, int]:
    return {int(r[0]): int(r[1]) for r in sql(url, "SELECT id, price FROM products")}


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
    """A project database with a committed products table on main."""
    name = f"app_{uuid.uuid4().hex[:10]}"
    doltgres_admin(f"CREATE DATABASE {name}")
    url = f"{TEST_DOLTGRES_URL}/{name}"
    sql(
        url,
        "CREATE TABLE products (id INT PRIMARY KEY, name VARCHAR(80), price INT)",
        "INSERT INTO products VALUES (1, 'tent', 50), (2, 'stove', 30)",
        "SELECT dolt_commit('-A', '-m', 'seed')",
    )
    monkeypatch.setenv("APP_DB_URL", url)
    yield url
    doltgres_admin(f"DROP DATABASE IF EXISTS {name}")


def provider(url: str) -> DoltgresBranchProvider:
    spec = DatabaseWorkspace(
        kind="doltgres", url_env="APP_DB_URL", database_env="DB_DATABASE", app_url_env="DB_URL"
    )
    (found,) = providers_for([spec], {"APP_DB_URL": url})
    assert isinstance(found, DoltgresBranchProvider)
    return found


def test_the_project_file_declares_a_doltgres_database() -> None:
    spec = parse_spec(f'name = "x"\n{DB_TOML}')
    (database,) = spec.workspace.database
    assert database.kind == "doltgres"
    (found,) = providers_for([database], {"APP_DB_URL": "postgresql+psycopg://u:p@h:5480/app"})
    assert isinstance(found, DoltgresBranchProvider)
    assert found.database == "app"
    assert found.branch_url(4).endswith("@h:5480/app/card-4")


# The provider ----------------------------------------------------------------------


@needs_doltgres
def test_a_card_works_on_its_own_branch(appdb: str) -> None:
    db = provider(appdb)
    base = db.default_head()
    head = db.create(7)
    assert db.exists(7) and head == base
    assert db.create(7) == head  # once only

    env = db.agent_env(7)
    assert env["DB_DATABASE"].endswith("/card-7")
    assert env["DB_URL"].startswith("postgresql://") and env["DB_URL"].endswith("/card-7")
    # What the agent's program writes lands on the card's branch only.
    sql(
        env["APP_DB_URL"],
        "UPDATE products SET price = 65 WHERE id = 1",
        "INSERT INTO products VALUES (3, 'lantern', 20)",
        "CREATE TABLE reviews (id INT PRIMARY KEY, body TEXT)",
    )
    assert prices(appdb) == {1: 50, 2: 30}
    new_head = db.commit(7, "card 7: reprice and add reviews")
    assert new_head and new_head != head
    assert db.commit(7, "nothing new") is None

    changes = {c.table: c for c in db.changes(7)}
    assert set(changes) == {"products", "reviews"}
    products = changes["products"]
    assert products.data_change and products.rows_added == 1 and products.rows_modified == 1
    assert changes["reviews"].diff_type == "added" and changes["reviews"].schema_change


@needs_doltgres
def test_merge_and_undo(appdb: str) -> None:
    db = provider(appdb)
    db.create(8)
    sql(db.agent_env(8)["APP_DB_URL"], "UPDATE products SET price = 99 WHERE id = 2")
    db.commit(8, "card 8")
    before, after = db.merge(8, "merge card 8")
    assert before != after and db.default_head() == after
    assert prices(appdb)[2] == 99
    db.undo_merge(before)
    assert db.default_head() == before and prices(appdb)[2] == 30
    with pytest.raises(WorkspaceError):
        db.undo_merge("not-a-commit")


@needs_doltgres
def test_a_conflict_aborts_the_merge_and_leaves_main_alone(appdb: str) -> None:
    db = provider(appdb)
    db.create(9)
    sql(db.agent_env(9)["APP_DB_URL"], "UPDATE products SET price = 70 WHERE id = 1")
    db.commit(9, "card 9")
    sql(
        appdb,
        "UPDATE products SET price = 80 WHERE id = 1",
        "SELECT dolt_commit('-A', '-m', 'someone else')",
    )
    main_before = db.default_head()
    with pytest.raises(DatabaseMergeConflict):
        db.merge(9, "merge card 9")
    assert db.default_head() == main_before and prices(appdb)[1] == 80


@needs_doltgres
def test_revert_tag_and_discard(appdb: str) -> None:
    db = provider(appdb)
    db.create(10)
    sql(db.agent_env(10)["APP_DB_URL"], "UPDATE products SET price = 11 WHERE id = 1")
    db.commit(10, "card 10")
    _, merge = db.merge(10, "merge card 10")
    db.tag("card-10-approved", merge, "landed")
    assert sql(appdb, "SELECT tag_name FROM dolt_tags") == [("card-10-approved",)]
    reverted = db.revert(merge)
    assert reverted != merge and prices(appdb)[1] == 50
    assert db.discard(10) and not db.exists(10) and not db.discard(10)


# Through the engine ----------------------------------------------------------------


@pytest.fixture
def project(project_factory: Callable[..., Project], appdb: str) -> Project:
    return project_factory(project_toml=project_toml(extra=DB_TOML))


def detail(db: Database, card_id: int, step: StepName) -> dict[str, Any]:
    with db.session() as session:
        run = store.latest_run(session, store.get_card(session, card_id))
        assert run is not None
        return dict(next(s for s in run.steps if s.name == step).detail)


async def land(settings: Settings, db: Database, project: Project, script: list[Any]) -> int:
    engine, _ = make_engine(settings, db, script)
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW, result.reason
    await engine.approve(card_id)
    return card_id


@needs_doltgres
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

    (database,) = detail(db, card_id, StepName.VERIFY)["databases"]
    (products,) = database["tables"]
    assert products["table"] == "products" and products["rows_modified"] == 1
    (request,) = reviewer.requests
    assert request.databases and request.databases[0]["tables"][0]["table"] == "products"
    assert prices(appdb)[1] == 50  # nothing has landed yet

    merged = await engine.approve(card_id)
    assert prices(appdb)[1] == 65
    agent = Path(project.agent_path)
    assert git(agent, "show", "main:pricing.py").startswith("OUTDOOR_MARKUP")
    with db.session() as session:
        events = store.events_for_card(session, store.get_card(session, card_id))
        event = next(e for e in events if e.kind == "card.merged")
    (data_merge,) = event.data["databases"]
    assert data_merge["merge"] == provider(appdb).default_head()
    assert event.data["commit"] == merged.commit


@needs_doltgres
async def test_approval_is_bound_to_the_verified_data(
    settings: Settings, db: Database, project: Project, appdb: str
) -> None:
    script = [update("UPDATE products SET price = 55 WHERE id = 1"), {"op": "finish"}]
    engine, _ = make_engine(settings, db, script)
    card_id = add_card(db, project)
    await engine.run_card(card_id)
    sql(
        provider(appdb).agent_env(card_id)["APP_DB_URL"],
        "UPDATE products SET price = 1 WHERE id = 2",
        "SELECT dolt_commit('-A', '-m', 'sneaky')",
    )
    with pytest.raises(ApproveError, match="moved since it was verified"):
        await engine.approve(card_id)
    assert prices(appdb) == {1: 50, 2: 30}


@needs_doltgres
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
    assert provider(appdb).default_head() == data_before and prices(appdb)[1] == 50


@needs_doltgres
async def test_cancel_drops_the_database_branch(
    settings: Settings, db: Database, project: Project, appdb: str
) -> None:
    script = [update("UPDATE products SET price = 5 WHERE id = 1"), {"op": "finish"}]
    engine, _ = make_engine(settings, db, script)
    card_id = add_card(db, project)
    await engine.run_card(card_id)
    assert provider(appdb).exists(card_id)
    assert await engine.cancel(card_id)
    assert not provider(appdb).exists(card_id) and prices(appdb)[1] == 50


@needs_doltgres
async def test_undo_and_rewind_a_landed_card(
    settings: Settings, db: Database, project: Project, appdb: str
) -> None:
    def script(path: str, statement: str) -> list[Any]:
        return [
            {"op": "write", "path": path, "content": f"# {path}\n"},
            update(statement),
            {"op": "finish"},
        ]

    first = await land(
        settings, db, project, script("a.py", "UPDATE products SET price = 65 WHERE id = 1")
    )
    second = await land(
        settings, db, project, script("b.py", "UPDATE products SET price = 35 WHERE id = 2")
    )
    assert prices(appdb) == {1: 65, 2: 35}

    result = undo_card(db, first)
    assert prices(appdb) == {1: 50, 2: 35} and len(result.databases) == 1
    with pytest.raises(CheckpointError, match="drops everything"):
        rewind_to(db, second)
    rewound = rewind_to(db, second, confirm=True)
    assert prices(appdb) == {1: 65, 2: 35}
    tags = [r[0] for r in sql(appdb, "SELECT tag_name FROM dolt_tags")]
    assert rewound.backup_tag in tags


@needs_doltgres
def test_writes_on_main_outside_a_card_are_committed_before_a_card_branches(appdb: str) -> None:
    db = provider(appdb)
    # A setup script's migration ran against main and nobody committed it.
    sql(appdb, "CREATE TABLE users (id INT PRIMARY KEY, email TEXT)")
    before = db.default_head()
    db.create(11)
    assert db.default_head() != before  # main's work was committed first
    sql(db.agent_env(11)["APP_DB_URL"], "ALTER TABLE users ADD COLUMN location TEXT")
    db.commit(11, "card 11: add location")
    changes = {c.table: c for c in db.changes(11)}
    # The review shows only what the card did, not the setup's tables as new.
    assert set(changes) == {"users"} and changes["users"].diff_type == "modified"
    db.merge(11, "card 11")
    columns = sql(
        appdb,
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'users'",
    )
    assert "location" in {str(r[0]) for r in columns}
