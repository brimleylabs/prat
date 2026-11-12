"""The deep scan: a tools-only inventory of every file, a reader per module, a synthesis, and the
graph that draws all of it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import add_card, git
from prat.config import Settings
from prat.db import Database, Project
from prat.knowledge.deepscan import SCAN_FILE, deep_seed
from prat.knowledge.entries import KnowledgeBase
from prat.knowledge.graph import build_graph
from prat.knowledge.inventory import build_inventory, load_inventory
from prat.runtime import AgentTask, FakeRuntime, FakeScript

pytestmark = pytest.mark.integration


def grow(repo: Path) -> str:
    """A small project: an app package, templates, docs, a data folder, a binary, and history in
    which two files keep changing together."""
    files = {
        "app/__init__.py": "",
        "app/db.py": "def connect():\n    return 'db'\n",
        "app/pages.py": "from pathlib import Path\n\nPAGES = Path('content')\n",
        "app/main.py": "from app.db import connect\n\n\ndef run():\n    return connect()\n",
        "templates/home.html": "<h1>{{ title }}</h1>\n",
        "requirements.txt": "fastapi==0.110.0\nsqlalchemy>=2.0\n",
        "pyproject.toml": '[project]\nname = "shop"\nrequires-python = ">=3.12"\n',
        "logo.png": "\x89PNG not really",
    }
    for i in range(25):
        files[f"content/page-{i:02d}.json"] = json.dumps({"title": f"Page {i}"})
    for rel, text in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "app")
    for n in range(3):
        for rel in ("app/main.py", "app/db.py"):
            (repo / rel).write_text((repo / rel).read_text() + f"# change {n}\n", encoding="utf-8")
        git(repo, "commit", "-q", "-am", f"change {n}")
    return git(repo, "rev-parse", "HEAD").strip()


def test_the_inventory_covers_every_file(tmp_path: Path, project: Project) -> None:
    repo = Path(project.checkout_path)
    head = grow(repo)
    inv = build_inventory(repo, head)
    kinds = {f.path: f.kind for f in inv.files}
    assert kinds["app/main.py"] == "code" and kinds["templates/home.html"] == "code"
    assert kinds["requirements.txt"] == "config" and kinds["logo.png"] == "binary"
    assert kinds["content/page-00.json"] == "data"
    names = {m.name: m for m in inv.modules}
    # Tiny folders (the root files, one template, the project file) share one reader.
    assert "app" in names and "(misc group 1)" in names
    assert "templates/home.html" in names["(misc group 1)"].files
    content = names["content"]
    assert content.data and content.data_count == 25 and len(content.files) == 3
    # Data is probed by tools: its shape and the code that loads it, without a model.
    assert content.kind == "data" and content.probe["shapes"] == ["{title: text}"]
    assert content.probe["used_by"] == ["app/pages.py"]
    assets = next(m for m in inv.modules if m.kind == "assets")
    assert assets.data_count == 1 and assets.files == []
    assert ["app/main.py", "app/db.py"] in inv.imports
    # Changed together in the first commit and the three after it.
    assert {"a": "app/db.py", "b": "app/main.py", "together": 4} in inv.cochange
    assert inv.hotspots[0]["commits"] >= 3
    assert {"name": "fastapi", "spec": "==0.110.0", "file": "requirements.txt"} in inv.declared
    assert {"what": "Python", "version": ">=3.12", "file": "pyproject.toml"} in inv.runtimes
    assert inv.languages["Python"]["files"] == 4
    summary = inv.summary()
    assert "Code modules:" in summary and "content/ holds 25 data files" in summary


def scripts(read: dict[str, list[str]]) -> Any:
    def script(task: AgentTask) -> FakeScript:
        module = task.metadata.get("module", "")
        if module == "(synthesis)":
            answer = {
                "deltas": [
                    {
                        "op": "add",
                        "kind": "convention",
                        "summary": "Requests go from app.main to app.db for every page",
                        "body": "run() calls connect() each time.",
                        "paths": ["app/main.py", "app/db.py"],
                    }
                ]
            }
            return FakeScript.parse([{"op": "finish", "message": json.dumps(answer)}])
        files = [
            line[2:].split(" (")[0] for line in task.prompt.splitlines() if line.startswith("- ")
        ]
        read[module] = files
        answer = {
            "summary": f"The {module} module. It holds {len(files)} files.",
            "deltas": [
                {
                    "op": "add",
                    "kind": "pitfall",
                    "summary": f"Keep {module} small and readable",
                    "body": "Noted while reading it.",
                    "paths": files[:1],
                }
            ],
        }
        steps: list[dict[str, Any]] = [{"op": "read", "path": f} for f in files]
        steps.append({"op": "finish", "message": json.dumps(answer)})
        return FakeScript.parse(steps)

    return script


async def test_a_deep_scan_reads_every_module_and_draws_the_project(
    settings: Settings, db: Database, project: Project
) -> None:
    grow(Path(project.checkout_path))
    read: dict[str, list[str]] = {}
    runtime = FakeRuntime(scripts(read), state_dir=settings.home / "fake-sessions")

    result = await deep_seed(db, settings, runtime, project.slug, reader_model="r", synth_model="s")

    assert result.error is None and not result.reader_errors
    assert result.files_read == result.readable  # every code, config and doc file was read
    # Only code, config and docs get a reader; data and assets were probed.
    assert "content" not in read and set(read) == {"app", "(misc group 1)"}
    assert set(result.timings) >= {"inventory", "read", "synthesis", "total"}
    kb = KnowledgeBase(settings.home, project.slug)
    entries = kb.entries()
    kinds = {e.kind for e in entries}
    assert {"layout", "pitfall", "convention", "stack"} <= kinds
    assert any(e.summary.startswith("app: ") for e in entries)
    stack = [e for e in entries if e.kind == "stack"]
    assert any("fastapi" in e.body for e in stack)
    assert json.loads((kb.root / SCAN_FILE).read_text())["files_read"] == result.files_read

    inv = load_inventory(kb.root)
    assert inv is not None
    graph = build_graph(entries, project=project.slug, inventory=inv).to_dict()
    ids = {n["id"] for n in graph["nodes"]}
    assert {"project", "module:app", "module:content", "file:app/main.py"} <= ids
    relations = {(e["source"], e["target"], e["relation"]) for e in graph["edges"]}
    assert ("file:app/main.py", "file:app/db.py", "imports") in relations
    assert ("file:app/db.py", "file:app/main.py", "changes_with") in relations
    assert ("file:app/main.py", "module:app", "in") in relations
    layout = next(e for e in entries if e.summary.startswith("app: "))
    assert (layout.id, "module:app", "about") in relations


async def test_the_dream_refreshes_a_scanned_inventory(
    settings: Settings, db: Database, project: Project
) -> None:
    from prat.knowledge.maintain import remember

    checkout = Path(project.checkout_path)
    grow(checkout)
    runtime = FakeRuntime(scripts({}), state_dir=settings.home / "fake-sessions")
    await deep_seed(db, settings, runtime, project.slug, reader_model=None, synth_model=None)
    kb = KnowledgeBase(settings.home, project.slug)
    before = load_inventory(kb.root)
    assert before is not None

    (checkout / "app" / "billing.py").write_text("def charge():\n    pass\n", encoding="utf-8")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "billing")
    agent = Path(project.agent_path)
    git(agent, "pull", "-q", "--ff-only", "origin", "main")
    add_card(db, project)  # remember needs nothing from it; it just runs per project

    report = await remember(db, settings.home, None)
    after = load_inventory(kb.root)
    assert report.projects[0].inventory_refreshed
    assert after is not None and after.commit != before.commit
    assert after.file("app/billing.py") is not None


async def test_the_knowledge_page_shows_the_scan_and_its_graph(
    settings: Settings, db: Database, project: Project
) -> None:
    from web_support import make_web

    grow(Path(project.checkout_path))
    runtime = FakeRuntime(scripts({}), state_dir=settings.home / "fake-sessions")
    await deep_seed(db, settings, runtime, project.slug, reader_model=None, synth_model=None)
    with make_web(settings, db, project) as web:
        page = web.client.get(f"/projects/{project.slug}/knowledge").text
        assert "What the scan found" in page and "Scan the code again" in page
        assert "data-kg-modules" in page and 'style="' not in page
        graph = web.client.get(f"/projects/{project.slug}/knowledge/graph.json").json()
        types = {n["type"] for n in graph["nodes"]}
        assert {"project", "module", "file", "entry"} <= types
        version = web.client.get(f"/projects/{project.slug}/knowledge/version").json()["version"]
        assert version == graph["version"]
