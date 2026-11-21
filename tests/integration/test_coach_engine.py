"""The coach in the engine: every run is read, the card is told, and the next builder on the
project gets the rules the last runs earned (prat.coach)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import select

from conftest import WRITE_GREET, add_card, git, make_engine
from prat import coach
from prat.config import Settings
from prat.db import Comment, Database, Project
from prat.domain import CardStatus, Verdict
from prat.engine import FakeReviewer
from prat.runtime import AgentTask, FakeRuntime, FakeScript

pytestmark = pytest.mark.integration

# Changes one line of the readme and flips its line ending on the way.
CRLF = [
    {"op": "write", "path": "README.md", "content": "# acme-api\r\nmore\r\n"},
    {"op": "finish", "message": "done"},
]


def recording(seen: list[AgentTask], script: list[dict[str, object]]) -> FakeRuntime:
    def script_for(task: AgentTask) -> FakeScript:
        seen.append(task)
        return FakeScript.parse(script)

    return FakeRuntime(script_for)


def coach_notes(db: Database, card_id: int) -> list[str]:
    with db.session() as session:
        found = session.scalars(
            select(Comment).where(Comment.card_id == card_id, Comment.kind == "coach")
        )
        return [comment.body for comment in found]


async def test_a_slip_is_put_right_noted_and_told_to_the_next_builder(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory()
    seen: list[AgentTask] = []
    engine, _ = make_engine(settings, db, recording(seen, CRLF), coach="on")
    first = add_card(db, project, title="Extend the readme")
    result = await engine.run_card(first)
    await engine.checkouts_set_up()
    assert result.card_status is CardStatus.REVIEW, result.reason
    # PRAT put the endings back: the diff is the one line added, not the file.
    agent = Path(project.agent_path)
    branch = git(agent, "branch", "--list", "card/*", "--format=%(refname:short)")
    assert git(agent, "diff", "--shortstat", "main", branch).startswith("1 file changed, 1 ins")
    book = coach.load(settings.home, project.slug)
    assert [r.slips for r in book.runs] == [{"line_endings": 1}] and book.runs[0].clean
    (said,) = coach_notes(db, first)
    assert "rewrote a file's line endings (1)" in said and "attention is close" in said
    assert "## From the coach" not in seen[0].system_prompt  # nothing known before the first run

    second = add_card(db, project, title="Add greet")
    seen.clear()
    engine, _ = make_engine(settings, db, recording(seen, WRITE_GREET), coach="on")
    assert (await engine.run_card(second)).card_status is CardStatus.REVIEW
    await engine.checkouts_set_up()
    assert "## From the coach" in seen[0].system_prompt
    assert coach.RULES["line_endings"][1] in seen[0].system_prompt
    assert coach_notes(db, second) == []  # a clean run: the coach has nothing to say


async def test_work_sent_back_counts_against_the_record_and_is_read_by_the_coach(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory()
    learned = '{"rules": [{"rule": "Test every error case the card names.", "evidence": "run 1"}]}'
    seen: list[AgentTask] = []

    def script_for(task: AgentTask) -> FakeScript:
        seen.append(task)
        if task.metadata.get("role") == "coach":
            return FakeScript.parse([{"op": "finish", "message": learned}])
        return FakeScript.parse(WRITE_GREET)

    reviewer = FakeReviewer(Verdict.NOT_MET, fix_list=["Add a test for the empty name."])
    engine, _ = make_engine(settings, db, FakeRuntime(script_for), reviewer=reviewer, coach="on")
    card_id = add_card(db, project)
    assert (await engine.run_card(card_id)).card_status is CardStatus.REWORK
    await engine.lessons_written()
    book = coach.load(settings.home, project.slug)
    assert book.runs[0].sent_back and not book.runs[0].clean
    assert book.runs[0].fixes == ["Add a test for the empty name."]
    assert [item.rule for item in book.learned] == ["Test every error case the card names."]
    assert coach.attention(book) == coach.CLOSE and book.reflected_to == book.runs[0].run
    (said,) = coach_notes(db, card_id)
    assert said.startswith("The reviewer sent it back.")
    # The coach's own session reads no files and has no tools.
    coaching = next(task for task in seen if task.metadata.get("role") == "coach")
    assert not coaching.allowed_tools and "Add a test for the empty name." in coaching.prompt


async def test_switched_off_the_coach_keeps_no_book(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory()
    engine, _ = make_engine(settings, db, CRLF, coach="off")
    card_id = add_card(db, project)
    assert (await engine.run_card(card_id)).card_status is CardStatus.REVIEW
    await engine.checkouts_set_up()
    assert coach.load(settings.home, project.slug).runs == [] and coach_notes(db, card_id) == []
