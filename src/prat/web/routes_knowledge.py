"""Project knowledge in the web UI: the graph, the entries, and a person's changes to them."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from prat import store
from prat.knowledge import curator
from prat.knowledge.deepscan import SCAN_FILE, deep_seed
from prat.knowledge.entries import (
    KINDS,
    Entry,
    EntryProblem,
    HistoryItem,
    KnowledgeBase,
    new_id,
    now_iso,
)
from prat.knowledge.graph import build_graph, code_map_for
from prat.knowledge.inventory import Inventory, load_inventory
from prat.model_registry import Role, resolve_model
from prat.web.app import render, web_context
from prat.web.routes_actions import _flash, _go, _text

router = APIRouter()

KIND_HELP = {
    "convention": "How this project does things",
    "pitfall": "What went wrong before",
    "command": "Commands that matter",
    "decision": "Choices made and why",
    "layout": "Where things live",
    "stack": "Runtimes, frameworks and versions",
}


@dataclass
class KnowledgePage:
    slug: str
    name: str
    entries: list[Entry] = field(default_factory=list)
    inactive: int = 0
    problems: list[EntryProblem] = field(default_factory=list)
    lint: list[str] = field(default_factory=list)
    held: list[dict[str, Any]] = field(default_factory=list)
    inventory: Inventory | None = None
    scan: dict[str, Any] | None = None
    history: list[HistoryItem] = field(default_factory=list)

    @property
    def confirmed(self) -> int:
        return sum(1 for e in self.entries if e.confirmed)

    @property
    def files(self) -> int:
        return len({p for e in self.entries for p in e.paths})

    def by_kind(self) -> dict[str, int]:
        counts = dict.fromkeys(KINDS, 0)
        for entry in self.entries:
            counts[entry.kind] = counts.get(entry.kind, 0) + 1
        return counts


def _project(request: Request, slug: str) -> tuple[str, str, Path, str]:
    ctx = web_context(request)
    with ctx.db.session() as session:
        try:
            project = store.get_project(session, slug)
        except store.NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        return project.slug, project.name, Path(project.agent_path), project.default_branch


def _kb(request: Request, slug: str) -> KnowledgeBase:
    return KnowledgeBase(web_context(request).settings.home, slug)


def _lint(kb: KnowledgeBase) -> list[str]:
    try:
        text = (kb.root / "LINT.md").read_text(encoding="utf-8")
    except OSError:
        return []
    issues = [line[2:].strip() for line in text.splitlines() if line.startswith("- ")]
    return [i for i in issues if i != "Nothing to report."]


def _scan(kb: KnowledgeBase) -> dict[str, Any] | None:
    try:
        value = json.loads((kb.root / SCAN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _inventory_commit(kb: KnowledgeBase) -> str | None:
    """The commit a deep scan's inventory describes, read without parsing the whole file."""
    path = kb.root / "inventory.json"
    try:
        with open(path, encoding="utf-8") as handle:
            head = handle.read(400)
    except OSError:
        return None
    found = re.search(r'"commit":\s*"([0-9a-f]*)"', head)
    return found.group(1) if found else None


def _version(kb: KnowledgeBase) -> str:
    """Changes whenever an entry file changes, so the page can tell when to redraw."""
    entries, problems = kb.load()
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda e: e.id):
        digest.update(entry.to_text().encode("utf-8"))
    digest.update(str(len(problems)).encode())
    inv = kb.root / "inventory.json"
    if inv.is_file():
        digest.update(str(inv.stat().st_mtime_ns).encode())
    return digest.hexdigest()[:16]


@router.get("/projects/{slug}/knowledge")
async def knowledge_page(request: Request, slug: str) -> Response:
    slug, name, _, _ = _project(request, slug)
    kb = _kb(request, slug)

    def load() -> KnowledgePage:
        entries, problems = kb.load()
        active = [e for e in entries if e.active]
        active.sort(key=lambda e: (not e.confirmed, -(e.helped - e.hurt), e.kind, e.summary))
        return KnowledgePage(
            slug,
            name,
            active,
            len(entries) - len(active),
            problems,
            _lint(kb),
            curator.held(kb),
            load_inventory(kb.root),
            _scan(kb),
            kb.history(limit=8),
        )

    page = await asyncio.to_thread(load)
    running = _seeding(request).get(slug)
    return render(
        request,
        "knowledge.html",
        {
            "page": page,
            "kinds": KINDS,
            "kind_help": KIND_HELP,
            "seeding": running is not None and not running.done(),
        },
        nav="projects",
    )


@router.get("/projects/{slug}/knowledge/graph.json")
async def knowledge_graph(request: Request, slug: str, inactive: int = 0) -> Response:
    slug, _, agent, branch = _project(request, slug)
    kb = _kb(request, slug)

    def load() -> dict[str, Any]:
        version = _version(kb)
        inventory = load_inventory(kb.root)
        code = None if inventory is not None else code_map_for(kb, agent, branch)
        data = build_graph(
            kb.entries(), code, include_inactive=bool(inactive), project=slug, inventory=inventory
        ).to_dict()
        commit = inventory.commit if inventory is not None else (code.commit if code else "")
        data["version"] = f"{version}:{commit[:10]}"
        return data

    data = await asyncio.to_thread(load)
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.get("/projects/{slug}/knowledge/version")
async def knowledge_version(request: Request, slug: str) -> Response:
    """A cheap check the graph page polls; it fetches the graph only when this changes."""
    slug, _, agent, branch = _project(request, slug)
    kb = _kb(request, slug)

    def load() -> str:
        from prat.git import Git

        scanned = _inventory_commit(kb)
        if scanned is not None:
            return f"{_version(kb)}:{scanned[:10]}"
        head = ""
        if agent.is_dir():
            shown = Git(agent).run("rev-parse", "--verify", "--quiet", branch, check=False)
            head = shown.stdout.strip()[:10] if shown.ok else ""
        return f"{_version(kb)}:{head}"

    return JSONResponse({"version": await asyncio.to_thread(load)})


@router.get("/projects/{slug}/knowledge/entries/{entry_id}")
async def knowledge_entry(request: Request, slug: str, entry_id: str) -> Response:
    slug, _, _, _ = _project(request, slug)
    kb = _kb(request, slug)

    def load() -> tuple[Entry | None, list[HistoryItem], dict[str, str]]:
        entry = kb.get(entry_id)
        if entry is None:
            return None, [], {}
        titles = {e.id: e.summary for e in kb.entries()}
        return entry, kb.history(entry_id, limit=10), titles

    entry, history, titles = await asyncio.to_thread(load)
    if entry is None:
        raise HTTPException(404, f"no entry {entry_id!r}")
    return render(
        request,
        "_knowledge_entry.html",
        {"slug": slug, "entry": entry, "history": history, "titles": titles},
    )


def _change(request: Request, slug: str, entry_id: str, action: str) -> str:
    kb = _kb(request, slug)
    entry = kb.get(entry_id)
    if entry is None:
        raise HTTPException(404, f"no entry {entry_id!r}")
    if action == "confirm":
        entry.confidence = "confirmed"
        message = "Confirmed by a person"
    elif action == "retire":
        entry.status = "retired"
        entry.invalid_at = now_iso()
        message = "Retired by a person"
    else:
        raise HTTPException(404, "unknown action")
    kb.save(entry)
    kb.write_index()
    kb.commit(f"{message}: {entry.summary}")
    return f"{message}: {entry.summary}"


@router.post("/projects/{slug}/knowledge/entries/{entry_id}/{action}")
async def knowledge_action(request: Request, slug: str, entry_id: str, action: str) -> Response:
    slug, _, _, _ = _project(request, slug)
    _flash(request, await asyncio.to_thread(_change, request, slug, entry_id, action), "ok")
    return _go(request, f"/projects/{slug}/knowledge")


@router.post("/projects/{slug}/knowledge/held/{held_id}/{action}")
async def knowledge_release(request: Request, slug: str, held_id: str, action: str) -> Response:
    """Let a held entry through (as a person's) or drop it."""
    if action not in ("accept", "drop"):
        raise HTTPException(404, "unknown action")
    slug, _, _, _ = _project(request, slug)
    kb = _kb(request, slug)

    def release() -> str:
        try:
            entry = curator.release(kb, held_id, accept=action == "accept")
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, f"cannot add it: {exc}") from exc
        kb.write_index()
        if entry is None:
            kb.commit("Dropped a held entry")
            return "Dropped."
        kb.commit(f"Let through by a person: {entry.summary}")
        return f"Added: {entry.summary}"

    _flash(request, await asyncio.to_thread(release), "ok")
    return _go(request, f"/projects/{slug}/knowledge")


def _seeding(request: Request) -> dict[str, asyncio.Task[Any]]:
    tasks: dict[str, asyncio.Task[Any]] = request.app.state.__dict__.setdefault("seeding", {})
    return tasks


def start_deep_scan(request: Request, slug: str) -> bool:
    """Start a deep scan of a project in the background; False when one is already running."""
    tasks = _seeding(request)
    running = tasks.get(slug)
    if running is not None and not running.done():
        return False
    ctx = web_context(request)
    models = ctx.settings.models
    tasks[slug] = asyncio.create_task(
        deep_seed(
            ctx.db,
            ctx.settings,
            ctx.engine.runtime,
            slug,
            reader_model=resolve_model(Role.READER, settings_models=models),
            synth_model=resolve_model(Role.SCOUT, settings_models=models),
        ),
        name=f"prat-seed-{slug}",
    )
    return True


@router.post("/projects/{slug}/knowledge/seed")
async def knowledge_seed(request: Request, slug: str) -> Response:
    """Start the scout on this project in the background (one at a time per project)."""
    slug, _, _, _ = _project(request, slug)
    if not start_deep_scan(request, slug):
        _flash(request, "The scout is already reading this project.", "info")
        return _go(request, f"/projects/{slug}/knowledge")
    _flash(
        request,
        "The deep scan has started: tools first, then a reader for every module, then a "
        "synthesis. Entries and the graph appear here as it finishes.",
        "ok",
    )
    return _go(request, f"/projects/{slug}/knowledge")


@router.post("/projects/{slug}/knowledge/entries")
async def knowledge_add(request: Request, slug: str) -> Response:
    slug, _, _, _ = _project(request, slug)
    form = await request.form()
    paths = [p.strip() for p in _text(form, "paths").replace(",", "\n").splitlines() if p.strip()]
    entry = Entry(
        id=new_id(),
        kind=_text(form, "kind"),
        summary=_text(form, "summary"),
        body=_text(form, "body"),
        paths=paths,
        author="person",
        confidence="confirmed",
    )
    try:
        entry.validate()
    except ValueError as exc:
        _flash(request, f"Not added: {exc}", "bad")
        return _go(request, f"/projects/{slug}/knowledge#add")
    kb = _kb(request, slug)

    def save() -> None:
        kb.ensure()
        kb.save(entry)
        kb.write_index()
        kb.commit(f"Added by a person: {entry.summary}")

    await asyncio.to_thread(save)
    _flash(request, f"Added: {entry.summary}", "ok")
    return _go(request, f"/projects/{slug}/knowledge")
