"""The engine writes its unexpected failures to the error log."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import select

from conftest import WRITE_GREET, add_card, make_engine, project_toml
from prat.config import Settings
from prat.db import Database, ErrorLog, Project
from prat.domain import CardStatus

pytestmark = pytest.mark.integration

MISSING_PROGRAM_TOML = """
name = "acme-api"
default_branch = "main"
autonomy = "medium"

[commands]
test = ['prat-no-such-program-xyz']

[verify]
gates = ["test"]
timeout_seconds = 30
"""


def errors(db: Database) -> list[ErrorLog]:
    with db.session() as session:
        return list(session.scalars(select(ErrorLog).order_by(ErrorLog.id)))


async def test_a_clean_run_logs_no_errors(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    engine, _ = make_engine(settings, db, WRITE_GREET)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW
    assert errors(db) == []


async def test_an_unexpected_step_failure_is_logged(
    settings: Settings, db: Database, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = make_engine(settings, db, WRITE_GREET)

    async def broken(*_args: Any) -> Any:
        raise RuntimeError("disk fell over at block 4096")

    monkeypatch.setattr(engine, "_step_verify", broken)
    first = add_card(db, project, "first")
    second = add_card(db, project, "second")
    for card_id in (first, second):
        result = await engine.run_card(card_id)
        assert result.card_status is CardStatus.FAILED

    rows = errors(db)
    assert [(row.source, row.kind) for row in rows] == [("engine", "RuntimeError")] * 2
    assert rows[0].message == "disk fell over at block 4096"
    assert rows[0].detail["step"] == "verify"
    assert rows[0].detail["card_id"] == first
    assert rows[0].detail["traceback"]
    # The same failure on another card counts as a repeat.
    assert rows[0].fingerprint == rows[1].fingerprint


async def test_an_agent_error_is_logged(settings: Settings, db: Database, project: Project) -> None:
    card_id = add_card(db, project)
    engine, _ = make_engine(settings, db, [{"op": "error", "message": "model overloaded"}])
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.FAILED
    (row,) = errors(db)
    assert (row.source, row.kind, row.message) == ("runtime", "agent_error", "model overloaded")
    assert row.detail["card_id"] == card_id


async def test_a_gate_that_cannot_start_is_logged(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory(project_toml=MISSING_PROGRAM_TOML)
    card_id = add_card(db, project)
    engine, _ = make_engine(settings, db, WRITE_GREET)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.FAILED
    assert result.reason and "could not start" in result.reason
    (row,) = errors(db)
    assert (row.source, row.kind) == ("gate", "gate_not_started")
    assert row.detail["gate"] == "test"
    assert "could not start" in row.message


async def test_a_gate_that_runs_and_fails_is_not_an_error(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory(project_toml=project_toml(test_code="import sys; sys.exit(1)"))
    card_id = add_card(db, project)
    engine, _ = make_engine(settings, db, WRITE_GREET)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.FAILED
    assert errors(db) == []
