from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from prat import store
from prat.config import Settings
from prat.db import Base, Card, Database, Run
from prat.db.migrate import current_revision, head_revision, is_current, upgrade
from prat.domain import CardStatus, InvalidTransition, RunStatus
from prat.projects import ProjectSpec


def test_upgrade_creates_all_tables(db: Database) -> None:
    tables = set(inspect(db.engine).get_table_names())
    expected = {"projects", "cards", "runs", "run_steps", "comments", "events"}
    assert expected | {"web_sessions", "login_nonces"} <= tables
    assert is_current(db)
    assert current_revision(db) == head_revision()


def test_migrations_match_models(db: Database) -> None:
    with db.engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


def test_upgrade_is_idempotent(db: Database) -> None:
    upgrade(db)
    assert is_current(db)


def test_fresh_database_is_not_current(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'x.db').as_posix()}")
    try:
        assert not is_current(database)
    finally:
        database.dispose()


def test_sqlite_uses_wal(db: Database) -> None:
    if db.engine.dialect.name != "sqlite":
        pytest.skip("SQLite only")
    with db.engine.connect() as connection:
        mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar()
    assert str(mode).lower() == "wal"


def _project(session: object, tmp_path: Path) -> object:
    return store.add_project(
        session,  # type: ignore[arg-type]
        slug="acme-api",
        checkout_path=tmp_path / "acme-api",
        agent_path=tmp_path / "acme-api-agent",
        spec=ProjectSpec(name="acme-api"),
    )


def test_model_validators_enforce_state_machine(db: Database, tmp_path: Path) -> None:
    with db.session() as session:
        project = _project(session, tmp_path)
        card = store.add_card(session, project, title="x")  # type: ignore[arg-type]
        assert card.status is CardStatus.DRAFT
        with pytest.raises(InvalidTransition):
            card.status = CardStatus.DONE
        store.set_card_status(session, card, CardStatus.READY)
    with db.session() as session:
        card = session.get(Card, card.id)
        assert card is not None
        assert card.status is CardStatus.READY
        events = store.events_for_card(session, card)
        assert [e.kind for e in events] == ["card.added", "card.status"]


def test_runs_must_start_queued() -> None:
    with pytest.raises(InvalidTransition):
        Run(status=RunStatus.RUNNING, runtime="fake")
    assert Run(status=RunStatus.QUEUED, runtime="fake").status is RunStatus.QUEUED


def test_cards_cannot_start_running() -> None:
    with pytest.raises(InvalidTransition):
        Card(title="x", status=CardStatus.RUNNING)


def test_overlapping_projects_rejected(db: Database, tmp_path: Path) -> None:
    with db.session() as session:
        _project(session, tmp_path)
        with pytest.raises(ValueError, match="already exists"):
            _project(session, tmp_path)
        with pytest.raises(ValueError, match="overlaps"):
            store.add_project(
                session,
                slug="other",
                checkout_path=tmp_path / "acme-api" / "sub",
                agent_path=tmp_path / "other-agent",
                spec=ProjectSpec(name="other"),
            )


def test_get_project_by_slug_or_id(db: Database, settings: Settings, tmp_path: Path) -> None:
    with db.session() as session:
        project = _project(session, tmp_path)
        assert store.get_project(session, "acme-api") is project
        assert store.get_project(session, project.id) is project  # type: ignore[attr-defined]
        with pytest.raises(store.NotFound):
            store.get_project(session, "nope")
