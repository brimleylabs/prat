"""A dream checks what PRAT tells agents against the project as it is now."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from conftest import git, make_repo
from prat.domain import Autonomy
from prat.git import Git
from prat.knowledge.entries import Entry, KnowledgeBase, new_id
from prat.knowledge.maintain import ProjectReport, lint
from prat.knowledge.reconcile import due, prat_rules, reconcile
from prat.projects import parse_spec
from prat.runtime import AgentTask, FakeRuntime, FakeScript

pytestmark = pytest.mark.integration

PROJECT = """\
name = "family"
autonomy = "medium"
[commands]
setup = "sh .prat/setup.sh"
test = "python -m pytest -q"
"""


class Checker:
    """Answers every check with ``answers``; records the prompts."""

    def __init__(self, answers: list[dict[str, Any]]) -> None:
        self.answers = answers
        self.tasks: list[AgentTask] = []

    def __call__(self, task: AgentTask) -> FakeScript:
        self.tasks.append(task)
        return FakeScript.parse([{"op": "finish", "message": json.dumps({"checks": self.answers})}])


def entry(summary: str, paths: list[str], *, kind: str = "pitfall", **kw: Any) -> Entry:
    return Entry(id=new_id(), kind=kind, summary=summary, paths=paths, **kw)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(
        tmp_path,
        "family",
        files={
            ".prat/project.toml": PROJECT,
            ".prat/setup.sh": "python3 -m venv .venv\n",
            "server/requirements.txt": "fastapi==0.162.5\npillow==13.0.0\n",
            "app.py": "print('hi')\n",
        },
    )


def head_of(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def test_prat_rules_are_read_from_the_real_policy() -> None:
    rules = prat_rules(parse_spec(PROJECT), Autonomy.MEDIUM)
    assert "Autonomy for agents on this project: medium." in rules
    assert "`curl -s http://127.0.0.1:8000/health`: allowed" in rules
    assert "`npx tsc --noEmit`: allowed" in rules
    assert "`pip install requests`: denied" in rules
    assert "`git push`: denied" in rules
    assert "the project's setup command, e.g. `sh .prat/setup.sh`: allowed" in rules
    low = prat_rules(parse_spec(PROJECT), Autonomy.LOW)
    assert "`python script.py`: denied" in low


async def test_entries_are_checked_against_the_project_as_it_is(tmp_path: Path, repo: Path) -> None:
    kb = KnowledgeBase(tmp_path / "home", "family")
    kb.ensure()
    no_setup = entry("No .prat/setup.sh exists, so nothing installs the server", [".prat/setup.sh"])
    old_pins = entry(
        "The server pins fastapi 0.115.6 and pillow 11.0.0",
        ["server/requirements.txt"],
        kind="stack",
    )
    npx = entry("npx is always denied at medium autonomy", [], kind="convention")
    fine = entry("app.py prints a greeting", ["app.py"], kind="layout")
    mine = entry("app.py must stay one line", ["app.py"], kind="convention", author="person")
    for item in (no_setup, old_pins, npx, fine, mine):
        kb.save(item)
    checker = Checker(
        [
            {"id": no_setup.id, "verdict": "wrong", "evidence": ".prat/setup.sh exists"},
            {
                "id": old_pins.id,
                "verdict": "refine",
                "evidence": "requirements.txt pins newer versions",
                "summary": "The server pins fastapi 0.162.5 and pillow 13.0.0",
                "body": "server/requirements.txt pins these exact versions.",
            },
            {"id": npx.id, "verdict": "wrong", "evidence": "npx tsc is allowed"},
            {"id": fine.id, "verdict": "true"},
            {"id": mine.id, "verdict": "wrong", "evidence": "app.py has one line still"},
        ]
    )
    runtime = FakeRuntime(checker, state_dir=tmp_path / "fake")
    head = head_of(repo)

    report = await reconcile(
        kb,
        repo,
        head,
        runtime,
        spec=parse_spec(PROJECT),
        autonomy=Autonomy.MEDIUM,
        model="reader-model",
    )

    assert (report.checked, report.true, report.wrong, report.refined) == (5, 1, 2, 1)
    assert kb.get(no_setup.id).status == "stale"  # type: ignore[union-attr]
    assert kb.get(npx.id).status == "stale"  # type: ignore[union-attr]
    assert kb.get(old_pins.id).status == "superseded"  # type: ignore[union-attr]
    current = [e for e in kb.entries() if e.active and e.kind == "stack"]
    assert [e.summary for e in current] == ["The server pins fastapi 0.162.5 and pillow 13.0.0"]
    # A person's entry is never changed; the doubt goes to the lint report.
    assert kb.get(mine.id).active  # type: ignore[union-attr]
    assert len(report.doubted_person) == 1
    result = ProjectReport("family")
    lint(kb, Counter(), result, report.doubted_person)
    assert "written by a person" in (kb.root / "LINT.md").read_text(encoding="utf-8")

    prompts = "\n".join(t.prompt for t in checker.tasks)
    assert "python3 -m venv .venv" in prompts  # the file as it is now
    assert "fastapi==0.162.5" in prompts
    assert "`npx tsc --noEmit`: allowed" in prompts  # PRAT's rules now
    assert all(t.model == "reader-model" and not t.allowed_tools for t in checker.tasks)

    # Nothing changed since: nothing is due, and no checker runs.
    later = datetime.now(UTC) + timedelta(days=1)
    assert due(kb, Git(repo), head, later) == []
    again = await reconcile(
        kb, repo, head, runtime, spec=parse_spec(PROJECT), autonomy=Autonomy.MEDIUM, model=None
    )
    assert again.checked == 0


async def test_an_entry_is_checked_again_when_its_files_change(tmp_path: Path, repo: Path) -> None:
    kb = KnowledgeBase(tmp_path / "home", "family")
    kb.ensure()
    about_app = entry("app.py prints a greeting", ["app.py"], kind="layout")
    about_server = entry("The server pins fastapi", ["server/requirements.txt"], kind="stack")
    for item in (about_app, about_server):
        kb.save(item)
    checker = Checker(
        [{"id": about_app.id, "verdict": "true"}, {"id": about_server.id, "verdict": "true"}]
    )
    runtime = FakeRuntime(checker, state_dir=tmp_path / "fake")
    spec = parse_spec(PROJECT)
    first = await reconcile(
        kb, repo, head_of(repo), runtime, spec=spec, autonomy=Autonomy.MEDIUM, model=None
    )
    assert first.checked == 2

    (repo / "app.py").write_text("print('hello')\n", encoding="utf-8", newline="\n")
    git(repo, "commit", "-q", "-am", "change app")
    second = await reconcile(
        kb, repo, head_of(repo), runtime, spec=spec, autonomy=Autonomy.MEDIUM, model=None
    )
    assert second.checked == 1
    assert about_app.summary in checker.tasks[-1].prompt
    assert about_server.summary not in checker.tasks[-1].prompt

    # Two weeks on, everything is due again.
    much_later = datetime.now(UTC) + timedelta(days=15)
    third = await reconcile(
        kb,
        repo,
        head_of(repo),
        runtime,
        spec=spec,
        autonomy=Autonomy.MEDIUM,
        model=None,
        now=much_later,
    )
    assert third.checked == 2


async def test_a_failed_checker_changes_nothing(tmp_path: Path, repo: Path) -> None:
    kb = KnowledgeBase(tmp_path / "home", "family")
    kb.ensure()
    item = entry("app.py prints a greeting", ["app.py"], kind="layout")
    kb.save(item)

    def broken(task: AgentTask) -> FakeScript:
        return FakeScript.parse([{"op": "error", "message": "model unavailable"}])

    runtime = FakeRuntime(broken, state_dir=tmp_path / "fake")
    report = await reconcile(
        kb,
        repo,
        head_of(repo),
        runtime,
        spec=parse_spec(PROJECT),
        autonomy=Autonomy.MEDIUM,
        model=None,
    )
    assert report.checked == 0 and kb.get(item.id).active  # type: ignore[union-attr]
    # Not marked checked: it is asked about again next pass.
    assert [e.id for e in due(kb, Git(repo), head_of(repo), datetime.now(UTC))] == [item.id]
