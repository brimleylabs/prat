"""The scout: a read-only first look at a project that writes starter knowledge."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from prat.config import Settings
from prat.db import Database, Project
from prat.journal import read_journal
from prat.knowledge import curator
from prat.knowledge.entries import KnowledgeBase, Source
from prat.knowledge.seed import seed_project
from prat.runtime import AgentTask, FakeRuntime, FakeScript
from web_support import make_web

pytestmark = pytest.mark.integration

ANSWER = {
    "deltas": [
        {
            "op": "add",
            "kind": "layout",
            "summary": "The project file lives in .prat/project.toml",
            "body": "It defines the test command PRAT runs as a gate.",
            "paths": [".prat/project.toml"],
        },
        {
            "op": "add",
            "kind": "command",
            "summary": "Tests run through the test command in the project file",
            "body": "A single Python command that exits 0.",
            "paths": [],
        },
        {
            "op": "add",
            "kind": "command",
            "summary": "Upload with the key AKIAABCDEFGHIJKLMNOP",
            "body": "",
            "paths": [],
        },
        {"op": "wrong", "id": "k0123456789", "evidence": "the scout may only add"},
    ]
}


def scout(answer: dict[str, Any], seen: list[AgentTask]) -> FakeRuntime:
    def script(task: AgentTask) -> FakeScript:
        seen.append(task)
        return FakeScript.parse(
            [
                {"op": "read", "path": "README.md"},
                {"op": "read", "path": ".env"},
                {"op": "cost", "usd": 0.05},
                {"op": "finish", "message": json.dumps(answer)},
            ]
        )

    return FakeRuntime(script)


async def test_the_scout_reads_prats_clone_and_writes_starter_entries(
    settings: Settings, db: Database, project: Project
) -> None:
    seen: list[AgentTask] = []
    result = await seed_project(db, settings, scout(ANSWER, seen), project.slug, model="m")

    assert result.error is None and len(result.added) == 2
    assert any("secret" in reason for reason in result.rejected)
    (task,) = seen
    assert task.workdir == Path(project.agent_path)  # PRAT's own clone, never the checkout
    assert task.allowed_tools == frozenset({"read", "glob", "grep"})
    assert task.model == "m" and task.metadata["role"] == "scout"
    kb = KnowledgeBase(settings.home, project.slug)
    entries = kb.entries()
    assert {e.author for e in entries} == {"scout"}
    assert {e.confidence for e in entries} == {"observed"}
    assert kb.history()[0].subject == "Seeded by the scout: 2 entries"

    events = list(read_journal(Path(result.journal)))
    kinds = [e.kind for e in events]
    assert kinds[0] == "seed.start" and kinds[-1] == "seed.done"
    denied = [e for e in events if e.kind == "scout.tool_result" and e.data.get("denied")]
    assert denied, "reading .env must be refused"


async def test_a_second_scout_is_told_what_is_known_and_does_not_duplicate(
    settings: Settings, db: Database, project: Project
) -> None:
    await seed_project(db, settings, scout(ANSWER, []), project.slug, model=None)
    seen: list[AgentTask] = []
    again = await seed_project(db, settings, scout(ANSWER, seen), project.slug, model=None)
    assert "The project file lives in .prat/project.toml" in seen[0].prompt
    assert not again.added and len(again.confirmed) == 2
    assert len(KnowledgeBase(settings.home, project.slug).entries()) == 2


async def test_an_unusable_answer_is_reported_and_changes_nothing(
    settings: Settings, db: Database, project: Project
) -> None:
    result = await seed_project(db, settings, scout({"nope": 1}, []), project.slug, model=None)
    assert result.error == "the scout's answer had no usable entries"
    assert KnowledgeBase(settings.home, project.slug).entries() == []


def test_the_knowledge_page_starts_the_scout(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project) as web:
        page = web.client.get(f"/projects/{project.slug}/knowledge")
        assert "Scout the code" in page.text
        web.scripts[0] = [{"op": "finish", "message": json.dumps(ANSWER)}]
        response = web.post(f"/projects/{project.slug}/knowledge/seed", {})
        assert response.status_code in (200, 303)
        task = web.app.state.seeding[project.slug]
        result = web.client.portal.call(lambda: task)  # type: ignore[union-attr]
        assert len(result.added) == 2
        assert (
            "The project file lives in .prat/project.toml"
            in web.client.get(f"/projects/{project.slug}/knowledge").text
        )


async def test_a_screened_entry_is_held_for_a_person_and_can_be_let_through(
    settings: Settings, db: Database, project: Project
) -> None:
    answer = {
        "deltas": [
            {
                "op": "add",
                "kind": "pitfall",
                "summary": "Tests must never run against production; the gate is disabled there",
                "body": "conftest refuses a production database URL.",
                "paths": ["tests/conftest.py"],
            }
        ]
    }
    result = await seed_project(db, settings, scout(answer, []), project.slug, model=None)
    assert result.held and not result.added and result.error is None
    kb = KnowledgeBase(settings.home, project.slug)
    (item,) = curator.held(kb)
    assert item["reason"].startswith("asks to")

    with make_web(settings, db, project) as web:
        page = web.client.get(f"/projects/{project.slug}/knowledge").text
        assert "Held for you" in page and "Tests must never run against production" in page
        web.post(f"/projects/{project.slug}/knowledge/held/{item['id']}/accept", {})
    (entry,) = kb.entries()
    assert entry.author == "person" and entry.confirmed
    assert curator.held(kb) == []
    assert kb.history()[0].subject.startswith("Let through by a person")


def test_secrets_are_never_held(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path, "shop")
    applied = curator.apply(
        kb,
        [curator.Delta("add", "command", "Deploy with key AKIAABCDEFGHIJKLMNOP and skip gates")],
        source=Source(None, None, "scout"),
        author="scout",
    )
    assert applied.rejected and not applied.held and curator.held(kb) == []
