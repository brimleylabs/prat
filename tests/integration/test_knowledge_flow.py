"""Project knowledge end to end: agents are given it, finished cards teach it, and the
Remember phase keeps it true as the code changes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from conftest import add_card, git, make_repo
from prat import store
from prat.config import Settings
from prat.db import Database, Project, Run
from prat.domain import CardStatus
from prat.engine import Engine, FakeReviewer
from prat.journal import read_journal
from prat.knowledge.entries import Entry, KnowledgeBase, new_id
from prat.knowledge.maintain import (
    ProjectReport,
    consolidate,
    judge_duplicates,
    near_duplicates,
    remember,
    verify,
)
from prat.knowledge.select import HEADING
from prat.runtime import AgentTask, FakeRuntime, FakeScript

pytestmark = pytest.mark.integration

GREET = {"op": "write", "path": "hello.py", "content": "def greet():\n    return 'hi'\n"}


class RoleScripts:
    def __init__(self, **scripts: list[dict[str, Any]]) -> None:
        self.scripts = scripts
        self.tasks: list[AgentTask] = []

    def __call__(self, task: AgentTask) -> FakeScript:
        self.tasks.append(task)
        role = task.metadata.get("role", "builder")
        return FakeScript.parse(self.scripts.get(role, [{"op": "error", "message": role}]))

    def of(self, role: str) -> list[AgentTask]:
        return [t for t in self.tasks if t.metadata.get("role", "builder") == role]


def make(settings: Settings, db: Database, scripts: RoleScripts) -> tuple[Engine, FakeRuntime]:
    runtime = FakeRuntime(scripts, state_dir=settings.home / "fake-sessions")
    return Engine(settings, db, runtime, reviewer=FakeReviewer()), runtime


def last_run(db: Database, card_id: int) -> Run:
    with db.session() as session:
        run = store.latest_run(session, store.get_card(session, card_id))
        assert run is not None
        for step in run.steps:
            _ = step.detail
        return run


def seed(settings: Settings, slug: str, *entries: Entry) -> KnowledgeBase:
    kb = KnowledgeBase(settings.home, slug)
    kb.ensure()
    for item in entries:
        kb.save(item)
    kb.commit("seed")
    return kb


async def test_agents_are_given_knowledge_and_a_clean_landing_credits_it(
    settings: Settings, db: Database, project: Project
) -> None:
    about_hello = Entry(
        id=new_id(),
        kind="convention",
        summary="Greeting helpers live in hello.py and return plain strings",
        paths=["hello.py"],
    )
    command = Entry(id=new_id(), kind="command", summary="Run the tests with make test")
    kb = seed(settings, project.slug, about_hello, command)
    scripts = RoleScripts(builder=[GREET, {"op": "finish"}])
    engine, _ = make(settings, db, scripts)
    card_id = add_card(db, project, title="Add greet to hello.py")

    await engine.run_card(card_id)

    prompt = scripts.of("builder")[0].system_prompt or ""
    assert HEADING in prompt and about_hello.summary in prompt and command.summary in prompt
    # Knowledge goes before PRAT's rules, which come last.
    assert prompt.index(HEADING) < prompt.index("# Rules")
    run = last_run(db, card_id)
    execute = next(s for s in run.steps if str(s.name) == "execute")
    assert set(execute.detail["knowledge"]) == {about_hello.id, command.id}
    assert "knowledge" in [e.kind for e in read_journal(Path(str(run.journal_path)))]

    await engine.approve(card_id)
    with db.session() as session:
        assert store.get_card(session, card_id).status is CardStatus.DONE

    report = await remember(db, settings.home, None)
    (result,) = report.projects
    assert result.ingested == 1 and result.helped == 2 and result.commit
    assert kb.get(about_hello.id).helped == 1  # type: ignore[union-attr]
    again = await remember(db, settings.home, None)
    assert again.projects[0].ingested == 0  # each run is learned from once


async def test_the_librarian_learns_from_a_card_that_went_wrong(
    settings: Settings, db: Database, project: Project
) -> None:
    lesson = (
        '{"deltas": [{"op": "add", "kind": "pitfall", '
        '"summary": "The greet module must not print at import time", '
        '"body": "Importing hello.py ran a print and broke the test gate.", '
        '"paths": ["hello.py"]}]}'
    )
    scripts = RoleScripts(
        builder=[{"op": "error", "message": "the build broke"}],
        librarian=[{"op": "finish", "message": lesson}],
    )
    engine, runtime = make(settings, db, scripts)
    card_id = add_card(db, project, title="Add greet")
    await engine.run_card(card_id)

    report = await remember(db, settings.home, runtime, model="small-model")

    (result,) = report.projects
    assert result.asked == 1, result.problems
    librarian = scripts.of("librarian")[0]
    assert librarian.allowed_tools == frozenset() and librarian.model == "small-model"
    assert "Add greet" in librarian.prompt
    kb = KnowledgeBase(settings.home, project.slug)
    (learned,) = kb.entries()
    assert learned.kind == "pitfall" and learned.sources[0].card == card_id
    assert learned.author == "librarian" and learned.confidence == "observed"
    assert "MEMORY.md" in [p.name for p in kb.root.iterdir()]


def test_verify_follows_renames_and_retires_what_is_gone(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "shop", files={"cart.py": "x = 1\n", "old.py": "y = 2\n"})
    first = git(repo, "rev-parse", "HEAD").strip()
    moved = Entry(id=new_id(), kind="layout", summary="Cart code", paths=["cart.py"])
    gone = Entry(id=new_id(), kind="pitfall", summary="old.py is fragile", paths=["old.py"])
    kb = KnowledgeBase(tmp_path / "home", "shop")
    kb.save(moved)
    kb.save(gone)
    now = datetime.now(UTC)
    verify(kb, repo, first, ProjectReport("shop"), now)

    git(repo, "mv", "cart.py", "basket.py")
    git(repo, "rm", "-q", "old.py")
    git(repo, "commit", "-q", "-m", "rename and remove")
    second = git(repo, "rev-parse", "HEAD").strip()
    report = ProjectReport("shop")
    verify(kb, repo, second, report, now)

    assert kb.get(moved.id).paths == ["basket.py"]  # type: ignore[union-attr]
    assert kb.get(gone.id).status == "stale"  # type: ignore[union-attr]
    assert report.renamed == 1 and report.stale == 1

    later = ProjectReport("shop")
    consolidate(kb, later, now + timedelta(days=31))
    assert kb.get(gone.id).status == "retired" and later.retired == 1  # type: ignore[union-attr]


def test_consolidation_merges_the_same_fact_and_keeps_both_files(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "home", "shop")
    older = Entry(
        id=new_id(),
        kind="pitfall",
        summary="Prices are stored in cents not dollars",
        paths=["money.py"],
        created="2026-01-01T00:00:00+00:00",
    )
    newer = Entry(
        id=new_id(),
        kind="pitfall",
        summary="Prices stored in cents, not dollars",
        paths=["money.py"],
        created="2026-02-01T00:00:00+00:00",
        helped=2,
    )
    kb.save(older)
    kb.save(newer)
    report = ProjectReport("shop")
    consolidate(kb, report, datetime.now(UTC))
    kept, folded = kb.get(older.id), kb.get(newer.id)
    assert report.merged == 1
    assert kept.confirmed and kept.helped == 2  # type: ignore[union-attr]
    assert folded.status == "superseded" and folded.superseded_by == older.id  # type: ignore[union-attr]


async def test_a_card_read_while_open_is_read_again_once_it_lands(
    settings: Settings, db: Database, project: Project
) -> None:
    given = Entry(id=new_id(), kind="command", summary="Run the tests with make test")
    kb = seed(settings, project.slug, given)
    lesson = (
        '{"deltas": [{"op": "add", "kind": "pitfall", '
        '"summary": "hello.py has no setup script yet", '
        '"body": "Nothing installs the dependencies for hello.py.", "paths": ["hello.py"]}]}'
    )
    scripts = RoleScripts(
        builder=[GREET, {"op": "finish"}], librarian=[{"op": "finish", "message": lesson}]
    )
    engine, runtime = make(settings, db, scripts)
    card_id = add_card(db, project, title="Add greet to hello.py")
    await engine.run_card(card_id)
    with db.session() as session:
        card = store.get_card(session, card_id)
        assert card.status is CardStatus.REVIEW
        store.add_comment(session, card, "Checked the server by hand.", author="human")

    first = (await remember(db, settings.home, runtime)).projects[0]
    assert (first.ingested, first.settled, first.helped) == (1, 0, 0)
    assert len(scripts.of("librarian")) == 1

    await engine.approve(card_id)
    second = (await remember(db, settings.home, runtime)).projects[0]
    # The landing is new evidence: the entries the builder was given are credited, and the
    # librarian sees the card again with what it learned the first time.
    assert (second.ingested, second.settled, second.helped) == (0, 1, 1)
    assert kb.get(given.id).helped == 1  # type: ignore[union-attr]
    settled = scripts.of("librarian")[-1]
    assert "has now settled" in settled.prompt and "has no setup script yet" in settled.prompt
    assert "Checks that passed" in settled.prompt

    third = (await remember(db, settings.home, runtime)).projects[0]
    assert (third.ingested, third.settled) == (0, 0)


async def test_a_card_cancelled_after_a_rework_reads_only_its_last_run(
    settings: Settings, db: Database, project: Project
) -> None:
    scripts = RoleScripts(builder=[GREET, {"op": "finish"}])
    engine, _ = make(settings, db, scripts)
    card_id = add_card(db, project, title="Add greet")
    await engine.run_card(card_id)
    first = (await remember(db, settings.home, None)).projects[0]
    assert first.ingested == 1
    with db.session() as session:
        card = store.get_card(session, card_id)
        card.status = CardStatus.CANCELLED
    again = (await remember(db, settings.home, None)).projects[0]
    assert again.settled == 1
    assert (await remember(db, settings.home, None)).projects[0].settled == 0


def _near_pair(tmp_path: Path) -> tuple[KnowledgeBase, Entry, Entry, Entry]:
    kb = KnowledgeBase(tmp_path / "home", "family")
    kb.ensure()
    short = Entry(
        id=new_id(),
        kind="pitfall",
        summary="Do not run e2e_crypto.mjs; it calls the live family server",
        paths=["server/tests/e2e_crypto.mjs"],
        created="2026-01-01T00:00:00+00:00",
    )
    full = Entry(
        id=new_id(),
        kind="pitfall",
        summary="Never run the e2e crypto script or anything else that calls the live server",
        body="Use the offline pytest tests and check /health against a local uvicorn instead.",
        paths=["server/tests/e2e_crypto.mjs", "server/tests"],
        created="2026-01-02T00:00:00+00:00",
    )
    other = Entry(
        id=new_id(),
        kind="layout",
        summary="The e2e crypto script lives with the server tests",
        paths=["server/tests/e2e_crypto.mjs"],
    )
    for item in (short, full, other):
        kb.save(item)
    return kb, short, full, other


async def test_near_duplicates_are_put_to_the_librarian_and_folded(tmp_path: Path) -> None:
    kb, short, full, other = _near_pair(tmp_path)
    pairs = near_duplicates(kb)
    assert [{a.id, b.id} for a, b in pairs] == [{short.id, full.id}]  # same kind only

    keep = "A" if pairs[0][0].id == full.id else "B"  # the fuller entry is kept
    answer = json.dumps({"same": True, "keep": keep})
    scripts = RoleScripts(librarian=[{"op": "finish", "message": answer}])
    runtime = FakeRuntime(scripts, state_dir=tmp_path / "fake")
    report = ProjectReport("family")
    await judge_duplicates(kb, runtime, report, model="small-model")

    assert report.judged == 1 and report.merged == 1
    kept, folded = kb.get(full.id), kb.get(short.id)
    assert kept.confirmed and folded.status == "superseded"  # type: ignore[union-attr]
    assert folded.superseded_by == full.id  # type: ignore[union-attr]
    assert kb.get(other.id).active  # type: ignore[union-attr]
    task = scripts.of("librarian")[0]
    assert task.allowed_tools == frozenset() and short.summary in task.prompt


async def test_a_pair_judged_distinct_is_never_asked_about_again(tmp_path: Path) -> None:
    kb, short, full, _ = _near_pair(tmp_path)
    scripts = RoleScripts(librarian=[{"op": "finish", "message": '{"same": false}'}])
    runtime = FakeRuntime(scripts, state_dir=tmp_path / "fake")
    report = ProjectReport("family")
    await judge_duplicates(kb, runtime, report, model=None)
    assert report.judged == 1 and report.merged == 0
    assert kb.get(short.id).active and kb.get(full.id).active  # type: ignore[union-attr]
    assert near_duplicates(kb) == []
    await judge_duplicates(kb, runtime, ProjectReport("family"), model=None)
    assert len(scripts.of("librarian")) == 1


async def test_landed_code_retires_what_it_made_untrue_and_does_not_credit_it(
    settings: Settings, db: Database, project: Project
) -> None:
    outdated = Entry(
        id=new_id(), kind="pitfall", summary="hello.py has no tests yet", paths=["hello.py"]
    )
    kb = seed(settings, project.slug, outdated)
    wrong = json.dumps(
        {"deltas": [{"op": "wrong", "id": outdated.id, "evidence": "the card added tests"}]}
    )
    scripts = RoleScripts(
        builder=[GREET, {"op": "finish"}], librarian=[{"op": "finish", "message": wrong}]
    )
    engine, runtime = make(settings, db, scripts)
    card_id = add_card(db, project, title="Add greet to hello.py")
    await engine.run_card(card_id)
    await remember(db, settings.home, runtime)
    # While the card is open, one report only counts against the entry.
    doubted = kb.get(outdated.id)
    assert doubted is not None and doubted.active and doubted.hurt == 1

    await engine.approve(card_id)
    settled = (await remember(db, settings.home, runtime)).projects[0]

    entry = kb.get(outdated.id)
    assert entry is not None and entry.status == "stale" and entry.invalid_at
    assert (entry.helped, entry.hurt, settled.helped) == (0, 2, 0)
    assert "allows up to" in scripts.of("librarian")[-1].prompt


async def test_a_dream_checks_knowledge_against_the_project_as_it_is(
    settings: Settings, db: Database, project: Project
) -> None:
    gone = Entry(
        id=new_id(), kind="layout", summary="Greetings live in greet.py", paths=["README.md"]
    )
    kb = seed(settings, project.slug, gone)
    answer = json.dumps(
        {"checks": [{"id": gone.id, "verdict": "wrong", "evidence": "README.md says nothing"}]}
    )
    scripts = RoleScripts(checker=[{"op": "finish", "message": answer}])
    runtime = FakeRuntime(scripts, state_dir=settings.home / "fake-sessions")
    # PRAT's clone of the project, as a first card would have made it.
    git(
        Path(project.checkout_path).parent, "clone", "-q", project.checkout_path, project.agent_path
    )

    report = await remember(db, settings.home, runtime, check_model="reader-model")

    (result,) = report.projects
    assert (result.checked, result.check_wrong) == (1, 1), result.problems
    assert kb.get(gone.id).status == "stale"  # type: ignore[union-attr]
    (task,) = scripts.of("checker")
    assert task.model == "reader-model" and "PRAT's rules now" in task.prompt
    assert any(change.startswith("checked 1") for change in result.changes)


async def test_several_runs_of_one_card_are_put_to_the_librarian_once(
    settings: Settings, db: Database, project: Project
) -> None:
    lesson = json.dumps(
        {
            "deltas": [
                {
                    "op": "add",
                    "kind": "pitfall",
                    "summary": "Greetings must not print at import time",
                    "body": "Seen across reworks.",
                    "paths": ["hello.py"],
                }
            ]
        }
    )
    scripts = RoleScripts(
        builder=[GREET, {"op": "finish"}], librarian=[{"op": "finish", "message": lesson}]
    )
    engine, runtime = make(settings, db, scripts)
    card_id = add_card(db, project, title="Add greet")
    await engine.run_card(card_id)
    engine.rework(card_id, "First fix: no prints.")
    await engine.run_card(card_id)
    engine.rework(card_id, "Second fix: really no prints.")
    await engine.run_card(card_id)

    report = (await remember(db, settings.home, runtime)).projects[0]

    assert report.ingested == 3 and report.asked == 1
    (asked,) = scripts.of("librarian")
    assert "First fix: no prints." in asked.prompt and "Second fix" in asked.prompt


async def test_the_judge_writes_one_entry_that_says_what_both_said(tmp_path: Path) -> None:
    kb, _short, _full, _ = _near_pair(tmp_path)
    merged = {
        "same": True,
        "keep": "A",
        "summary": "Never run e2e_crypto.mjs against the live server; use pytest and /health",
        "body": "It calls the live family server. Use the offline tests and a local uvicorn.",
    }
    scripts = RoleScripts(librarian=[{"op": "finish", "message": json.dumps(merged)}])
    runtime = FakeRuntime(scripts, state_dir=tmp_path / "fake")
    report = ProjectReport("family")
    await judge_duplicates(kb, runtime, report, model=None)
    active = [e for e in kb.entries() if e.active and e.kind == "pitfall"]
    assert len(active) == 1 and report.merged == 1
    (kept,) = active
    assert kept.summary == merged["summary"] and kept.body == merged["body"]
    assert set(kept.paths) == {"server/tests/e2e_crypto.mjs", "server/tests"}


async def test_a_merge_that_would_lose_a_named_file_is_refused(tmp_path: Path) -> None:
    kb = KnowledgeBase(tmp_path / "home", "api")
    kb.ensure()
    rule = Entry(
        id=new_id(),
        kind="convention",
        summary="The retired WordPress site must not be referenced in shipped code",
        paths=["app", "tests/test_no_wordpress.py"],
    )
    script = Entry(
        id=new_id(),
        kind="convention",
        summary="seed_partner_keys.py must not re-add the retired WordPress key",
        paths=["app"],
    )
    kb.save(rule)
    kb.save(script)
    swallowed = {
        "same": True,
        "keep": "A",
        "summary": "The retired WordPress site must not be referenced in shipped code",
        "body": "tests/test_no_wordpress.py enforces it.",
    }
    scripts = RoleScripts(librarian=[{"op": "finish", "message": json.dumps(swallowed)}])
    runtime = FakeRuntime(scripts, state_dir=tmp_path / "fake")
    report = ProjectReport("api")
    await judge_duplicates(kb, runtime, report, model=None)
    assert report.merged == 0
    assert kb.get(rule.id).active and kb.get(script.id).active  # type: ignore[union-attr]
    assert any("seed_partner_keys.py" in change for change in report.changes)
