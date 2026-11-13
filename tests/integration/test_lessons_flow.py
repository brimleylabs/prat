"""Lessons end to end: a card that reaches review gets a checked lesson, written in the
background, stored on the card and paid for by its run."""

from __future__ import annotations

import json

import pytest

from conftest import add_card
from prat import store
from prat.config import Settings
from prat.db import Database, Project
from prat.domain import CardStatus
from prat.engine import Engine, FakeReviewer
from prat.lessons import familiarity, latest, set_familiarity
from prat.runtime import AgentTask, FakeRuntime, FakeScript

pytestmark = pytest.mark.integration

GREET = {"op": "write", "path": "hello.py", "content": "def greet():\n    return 'hi'\n"}


def lesson_answer(code: str) -> str:
    return json.dumps(
        {
            "title": "Small pure functions",
            "idea": "A function that returns a value instead of printing is easy to test.",
            "happened": "The card asked for a greeting helper; the change adds greet().",
            "excerpts": [{"file": "hello.py", "code": code, "note": "returns, never prints"}],
            "why": "Returning keeps the caller in charge of output.",
            "alternatives": ["Print inside greet(): harder to test."],
            "question": "How would you test greet()?",
            "answer": "Assert on its return value.",
            "tags": ["pure functions"],
        }
    )


class Scripts:
    def __init__(self, teacher: list[str]) -> None:
        self.teacher = teacher
        self.tasks: list[AgentTask] = []

    def __call__(self, task: AgentTask) -> FakeScript:
        self.tasks.append(task)
        role = task.metadata.get("role", "builder")
        if role == "teacher":
            message = self.teacher.pop(0) if self.teacher else "no lesson"
            return FakeScript.parse([{"op": "finish", "message": message}])
        return FakeScript.parse([GREET, {"op": "finish", "message": "Added greet"}])

    def of(self, role: str) -> list[AgentTask]:
        return [t for t in self.tasks if t.metadata.get("role", "builder") == role]


def engine_for(settings: Settings, db: Database, scripts: Scripts, lessons: str) -> Engine:
    settings = settings.model_copy(update={"lessons": lessons})
    runtime = FakeRuntime(scripts, state_dir=settings.home / "fake-sessions")
    return Engine(settings, db, runtime, reviewer=FakeReviewer())


async def test_a_card_in_review_gets_a_checked_lesson(
    settings: Settings, db: Database, project: Project
) -> None:
    scripts = Scripts([lesson_answer("    return 'hi'")])
    engine = engine_for(settings, db, scripts, "review")
    card_id = add_card(db, project, title="Add greet to hello.py")

    result = await engine.run_card(card_id)
    await engine.lessons_written()

    assert result.card_status == CardStatus.REVIEW
    (teacher,) = scripts.of("teacher")
    assert teacher.allowed_tools == frozenset()
    assert "def greet():" in teacher.prompt and "Add greet to hello.py" in teacher.prompt
    assert "familiarity with this stack: working" in teacher.prompt
    found = latest(db, card_id)
    assert found is not None
    lesson, data = found
    assert lesson.checked and lesson.dropped == 0 and len(lesson.excerpts) == 1
    assert data["head_commit"] and data["model"] == "claude-sonnet-5-5"
    with db.session() as session:
        (comment,) = [c for c in store.get_card(session, card_id).comments if c.kind == "lesson"]
        assert str(comment.author) == "system" and "## The idea" in comment.body


async def test_an_invented_excerpt_is_dropped_and_the_teacher_asked_again(
    settings: Settings, db: Database, project: Project
) -> None:
    scripts = Scripts(
        [lesson_answer("print('hi')  # never written"), lesson_answer("def greet():")]
    )
    engine = engine_for(settings, db, scripts, "review")
    card_id = add_card(db, project, title="Add greet")

    await engine.run_card(card_id)
    await engine.lessons_written()

    assert len(scripts.of("teacher")) == 2
    found = latest(db, card_id)
    assert found is not None and found[0].checked
    assert [e.code for e in found[0].excerpts] == ["def greet():"]


async def test_no_lesson_when_lessons_are_off_and_a_bad_answer_never_fails_the_run(
    settings: Settings, db: Database, project: Project
) -> None:
    scripts = Scripts([])
    engine = engine_for(settings, db, scripts, "off")
    card_id = add_card(db, project, title="Add greet")
    await engine.run_card(card_id)
    await engine.lessons_written()
    assert scripts.of("teacher") == [] and latest(db, card_id) is None

    engine = engine_for(settings, db, scripts, "review")
    other = add_card(db, project, title="Add greet again")
    result = await engine.run_card(other)
    await engine.lessons_written()
    assert result.card_status == CardStatus.REVIEW and latest(db, other) is None


async def test_familiarity_is_kept_per_stack_and_checked(
    settings: Settings, db: Database, project: Project
) -> None:
    set_familiarity(db, "Python", "new")
    assert familiarity(db) == {"python": "new"}
    with pytest.raises(ValueError, match="new, working, expert"):
        set_familiarity(db, "Python", "guru")
