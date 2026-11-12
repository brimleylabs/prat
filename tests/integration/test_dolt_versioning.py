"""Commits, tags and history of PRAT's records on the Dolt store.

Runs when PRAT_TEST_DOLT_URL points at a Dolt server; on SQLite only the
no-op behaviour is checked.
"""

from __future__ import annotations

import pytest

from conftest import TEST_DOLT_URL, WRITE_GREET, add_card, make_engine
from prat.config import Settings
from prat.db import Database, Project
from prat.dolt import versioning

pytestmark = pytest.mark.integration
needs_dolt = pytest.mark.skipif(not TEST_DOLT_URL, reason="set PRAT_TEST_DOLT_URL to a Dolt server")


def test_on_sqlite_every_call_is_a_no_op(db: Database) -> None:
    if TEST_DOLT_URL:
        pytest.skip("SQLite only")
    assert not versioning.is_dolt(db)
    assert versioning.commit(db, "nothing") is None
    assert versioning.tag(db, "x") is None
    assert versioning.head(db) is None
    with pytest.raises(versioning.NeedsDolt):
        versioning.history(db)


@needs_dolt
async def test_every_step_and_the_approval_are_commits(
    settings: Settings, db: Database, project: Project
) -> None:
    assert versioning.is_dolt(db)
    card_id = add_card(db, project)
    engine, _ = make_engine(settings, db, WRITE_GREET)
    await engine.run_card(card_id)
    merged = await engine.approve(card_id)

    messages = [c.message for c in versioning.history(db, limit=50)]
    steps = [m for m in messages if m.startswith(f"run 1 card {card_id} step ")]
    for name in ("prepare", "execute", "verify", "critique", "summarize", "finalize"):
        assert any(f"step {name}:" in m for m in steps), (name, steps)
    approval = next(m for m in messages if m.startswith(f"card {card_id} approved"))
    # The record commit names the git merge it belongs to.
    assert f"git {merged.commit}" in approval
    with db.engine.connect() as connection:
        tags = [row[0] for row in connection.exec_driver_sql("SELECT tag_name FROM dolt_tags")]
    assert f"card-{card_id}-approved" in tags
    # The author is the actor.
    first = versioning.history(db, limit=1, grep="approved")[0]
    assert first.message == approval


@needs_dolt
async def test_history_shows_what_changed_between_commits(
    settings: Settings, db: Database, project: Project
) -> None:
    before = versioning.head(db)
    assert before
    card_id = add_card(db, project)
    assert versioning.commit(db, f"card {card_id} created", actor="person")
    after = versioning.head(db)
    assert after and after != before
    changed = dict(versioning.changed_tables(db, before, after))
    assert "cards" in changed
    # Committing again with nothing new is not an error and makes no commit.
    assert versioning.commit(db, "nothing new") is None
    assert versioning.head(db) == after


@needs_dolt
def test_messages_are_one_line_and_capped(db: Database) -> None:
    from prat.db.models import AppSetting

    with db.session() as session:
        session.add(AppSetting(key="improve.enabled", value=True))
    commit = versioning.commit(db, "line one\nline two " + "x" * 2000, actor="web <ui>")
    assert commit
    latest = versioning.history(db, limit=1)[0]
    assert "\n" not in latest.message
    assert len(latest.message) <= versioning.MAX_MESSAGE_CHARS
