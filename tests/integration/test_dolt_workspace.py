"""A Dolt branch per card for a project's database.

Runs when PRAT_TEST_DOLT_URL points at a Dolt server; each test makes its own
project database there and drops it afterwards.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text

from conftest import TEST_DOLT_URL, dolt_admin
from prat.projects import DatabaseWorkspace, ProjectFileError, parse_spec
from prat.workspace import DatabaseMergeConflict, DoltBranchProvider, WorkspaceError

pytestmark = pytest.mark.integration
needs_dolt = pytest.mark.skipif(not TEST_DOLT_URL, reason="set PRAT_TEST_DOLT_URL to a Dolt server")


def run(url: str, *statements: str) -> list[object]:
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    out: list[object] = []
    try:
        with engine.connect() as connection:
            for sql in statements:
                result = connection.execute(text(sql))
                if result.returns_rows:
                    out.extend(result.all())
    finally:
        engine.dispose()
    return out


@pytest.fixture
def appdb() -> Iterator[str]:
    """A project database with a committed products table on main."""
    name = f"app_{uuid.uuid4().hex[:10]}"
    dolt_admin(f"CREATE DATABASE `{name}`")
    url = f"{TEST_DOLT_URL.rstrip('/')}/{name}"
    run(
        url,
        "CREATE TABLE products (id INT PRIMARY KEY, name VARCHAR(80), price INT)",
        "INSERT INTO products VALUES (1, 'tent', 50), (2, 'stove', 30)",
        "CALL DOLT_COMMIT('-A', '-m', 'seed')",
    )
    yield url
    dolt_admin(f"DROP DATABASE IF EXISTS `{name}`")


def provider(url: str) -> DoltBranchProvider:
    spec = DatabaseWorkspace(url_env="APP_DB_URL", database_env="DB_DATABASE", app_url_env="DB_URL")
    return DoltBranchProvider.from_spec(spec, {"APP_DB_URL": url})


def test_the_project_file_declares_databases_by_variable_name() -> None:
    spec = parse_spec(
        'name = "x"\n[[workspace.database]]\nurl_env = "APP_DB_URL"\ndatabase_env = "DB_DATABASE"\n'
    )
    (database,) = spec.workspace.database
    assert database.kind == "dolt" and database.default_branch == "main"
    for bad in ('url_env = "lower"', 'url_env = "A B"', 'url_env = "X"\nkind = "oracle"'):
        with pytest.raises(ProjectFileError):
            parse_spec(f'name = "x"\n[[workspace.database]]\n{bad}\n')


def test_a_missing_variable_is_a_clear_error() -> None:
    spec = DatabaseWorkspace(url_env="APP_DB_URL")
    with pytest.raises(WorkspaceError, match="set APP_DB_URL"):
        DoltBranchProvider.from_spec(spec, {})


@needs_dolt
def test_a_card_works_on_its_own_branch(appdb: str) -> None:
    db = provider(appdb)
    base = db.default_head()
    head = db.create(7)
    assert db.exists(7) and head == base
    assert db.create(7) == head  # once only

    env = db.agent_env(7)
    assert env["DB_DATABASE"].endswith("/card-7")
    assert env["DB_URL"].startswith("mysql://") and env["DB_URL"].endswith("/card-7")
    # What the agent's program writes lands on the card's branch only.
    run(
        env["APP_DB_URL"],
        "UPDATE products SET price = 65 WHERE id = 1",
        "INSERT INTO products VALUES (3, 'lantern', 20)",
        "ALTER TABLE products ADD COLUMN stock INT",
        "CREATE TABLE reviews (id INT PRIMARY KEY, body TEXT)",
    )
    assert run(appdb, "SELECT price FROM products WHERE id = 1") == [(50,)]
    new_head = db.commit(7, "card 7: reprice and add stock")
    assert new_head and new_head != head
    assert db.commit(7, "nothing new") is None

    changes = {c.table: c for c in db.changes(7)}
    assert set(changes) == {"products", "reviews"}
    products = changes["products"]
    assert products.schema_change and products.data_change
    assert products.rows_added == 1 and products.rows_modified == 1
    assert changes["reviews"].diff_type == "added"


@needs_dolt
def test_merge_and_undo(appdb: str) -> None:
    db = provider(appdb)
    db.create(8)
    run(db.agent_env(8)["APP_DB_URL"], "UPDATE products SET price = 99 WHERE id = 2")
    db.commit(8, "card 8")
    before, after = db.merge(8, "merge card 8")
    assert before != after and db.default_head() == after
    assert run(appdb, "SELECT price FROM products WHERE id = 2") == [(99,)]
    db.undo_merge(before)
    assert db.default_head() == before
    assert run(appdb, "SELECT price FROM products WHERE id = 2") == [(30,)]
    with pytest.raises(WorkspaceError):
        db.undo_merge("not-a-commit")


@needs_dolt
def test_a_conflict_aborts_the_merge_and_leaves_main_alone(appdb: str) -> None:
    db = provider(appdb)
    db.create(9)
    run(db.agent_env(9)["APP_DB_URL"], "UPDATE products SET price = 70 WHERE id = 1")
    db.commit(9, "card 9")
    run(
        appdb,
        "UPDATE products SET price = 80 WHERE id = 1",
        "CALL DOLT_COMMIT('-A', '-m', 'someone else')",
    )
    main_before = db.default_head()
    with pytest.raises(DatabaseMergeConflict):
        db.merge(9, "merge card 9")
    assert db.default_head() == main_before
    assert run(appdb, "SELECT price FROM products WHERE id = 1") == [(80,)]


@needs_dolt
def test_discard_drops_the_branch(appdb: str) -> None:
    db = provider(appdb)
    db.create(10)
    assert db.discard(10)
    assert not db.exists(10)
    assert not db.discard(10)
