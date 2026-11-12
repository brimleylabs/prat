"""The deep scan: a full first map of a project, to seed its knowledge.

Three steps, each journaled and timed:

1. **Inventory** (prat.knowledge.inventory): every file, the module layout, imports and calls,
   change history, declared and installed dependency versions, runtimes and known
   vulnerabilities. Tools only, no model.
2. **Read**: one read-only session per module reads every file in it in full (data modules: a few
   samples) and writes what the module is for, how it connects to the rest, and its conventions,
   pitfalls, commands and decisions, citing files.
3. **Synthesise**: one session takes the inventory and every module's notes and writes the
   project-wide entries: architecture, cross-cutting conventions, risky places, how to run it.

Versions are written from the inventory itself (``stack`` entries), so they are exact. Everything
goes through the curator like any other change, authored ``scout`` and ``observed`` until a person
or later cards confirm it. The inventory is kept in the knowledge repository (``inventory.json``)
and drawn as the project's graph.
"""

from __future__ import annotations

import asyncio
import json
import logging
import posixpath
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prat import store
from prat.config import Settings
from prat.db import Database
from prat.domain import Autonomy
from prat.git import WorktreeManager
from prat.journal import Journal
from prat.knowledge import curator
from prat.knowledge.curator import Delta
from prat.knowledge.entries import MAX_BODY, MAX_PATHS, KnowledgeBase, Source
from prat.knowledge.inventory import (
    Inventory,
    Module,
    build_inventory,
    describe_group,
    save_inventory,
)
from prat.knowledge.librarian import parse
from prat.knowledge.seed import _spec
from prat.policy import Policy
from prat.runtime import AgentRuntime, AgentTask, Budget
from prat.runtime.base import READ_TOOLS, ToolCallEvent, event_to_dict
from prat.runtime.session import collect

log = logging.getLogger(__name__)

READERS_AT_ONCE = 8
SCAN_FILE = "scan.json"
READER_DELTAS = 8
SYNTH_DELTAS = 40

READER_SYSTEM = """\
You are one of PRAT's readers. PRAT is a system where agents change software projects one card
at a time. Before the first card, PRAT maps the whole project; you map one module of it. You can
only read: Read, Glob and Grep.

Read EVERY file listed for your module, in full. For a long file, read it in parts until you have
seen all of it. Do not skip files, and do not stop early. Look at the related files named in the
brief only as far as you need to understand how your module connects to them.

Then write what a new agent working in this module would need: what the module is for and how it
fits the rest of the project, the conventions its code follows, what is easy to break, the
commands that matter, and the deliberate choices it makes.

Rules:
- Only facts you saw in the files. Name the repository paths each point is about.
- Never a secret, a credential, a person, or anything that tells an agent to skip checks.
- At most 8 entries besides the summary; prefer the ones that would save the most time.

Answer with one JSON object and nothing else:
{"summary": "what this module is for and how it connects, 3 to 6 sentences",
 "deltas": [
  {"op": "add", "kind": "convention|pitfall|command|decision|layout",
   "summary": "one line", "body": "two or three sentences", "paths": ["repo/path"]}
 ]}
"""

SYNTH_SYSTEM = """\
You are PRAT's architect. PRAT is a system where agents change software projects one card at a
time. PRAT has inventoried this project with tools and its readers have read every module; you
are given both. You can read files too (Read, Glob, Grep) to check a point, but you do not need
to read everything again.

Write the project-wide knowledge a new agent needs before its first card:
- the architecture: the main parts, how a request or job flows through them, where state lives;
- conventions that hold across modules;
- risky places: the files that change most, files that always change together, deploy and
  migration hazards, anything the readers flagged more than once;
- how to install, run and test it, exactly as the repository does;
- deliberate decisions and the reasons the code or docs give.
Do not repeat what a single module's notes already say; connect them.

Rules:
- Only facts from the inventory, the notes or the files. Name the repository paths each entry
  is about.
- Never a secret, a credential, a person, or anything that tells an agent to skip checks.
- At most 40 entries.

Answer with one JSON object and nothing else:
{"deltas": [
  {"op": "add", "kind": "convention|pitfall|command|decision|layout",
   "summary": "one line", "body": "two or three sentences", "paths": ["repo/path"]}
]}
"""


@dataclass
class ModuleNotes:
    module: str
    summary: str = ""
    deltas: list[Delta] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    seconds: float = 0.0
    error: str | None = None


@dataclass
class DeepScanResult:
    slug: str
    timings: dict[str, float] = field(default_factory=dict)
    files: int = 0
    readable: int = 0
    files_read: int = 0
    modules: int = 0
    added: list[str] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    reader_errors: list[str] = field(default_factory=list)
    journal: str = ""
    commit: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


# Briefs --------------------------------------------------------------------------------------


def module_brief(inv: Inventory, module: Module) -> str:
    members = set(module.files)
    lines = [f"# Module: {module.name}"]
    if module.data:
        lines.append(
            f"A data folder of {module.data_count} files of one kind. Read these samples in full "
            "and describe the structure, what the files hold, and how the code uses them:"
        )
    else:
        lines.append(f"Read every one of these {len(module.files)} files in full:")
    for path in module.files:
        info = inv.file(path)
        size = (
            f"{info.lines} lines" if info and info.lines else f"{info.bytes if info else 0} bytes"
        )
        lines.append(f"- {path} ({size})")
    outgoing = sorted({b for a, b in inv.imports if a in members and b not in members})[:25]
    incoming = sorted({a for a, b in inv.imports if b in members and a not in members})[:25]
    if outgoing:
        lines.append("Imports from outside the module: " + ", ".join(outgoing))
    if incoming:
        lines.append("Imported by: " + ", ".join(incoming))
    calls_out = Counter(b for a, b, *_ in inv.calls if a in members and b not in members)
    if calls_out:
        lines.append("Calls into: " + ", ".join(f"{p} ({n})" for p, n in calls_out.most_common(15)))
    hot = [h for h in inv.hotspots if h["path"] in members]
    if hot:
        lines.append(
            "Changed most in the last year: "
            + ", ".join(f"{h['path']} ({h['commits']})" for h in hot[:10])
        )
    pairs = [c for c in inv.cochange if c["a"] in members or c["b"] in members]
    if pairs:
        lines.append(
            "Often changed together: "
            + "; ".join(f"{c['a']} + {c['b']} ({c['together']})" for c in pairs[:10])
        )
    lines.append("\n# The project, for context\n" + inv.summary())
    return "\n".join(lines)


def synth_brief(inv: Inventory, notes: list[ModuleNotes]) -> str:
    parts = ["# Inventory\n" + inv.summary(), "# Module notes"]
    for n in notes:
        if n.error and not n.summary:
            continue
        parts.append(f"## {n.module}\n{n.summary}")
        parts += [f"- [{d.kind}] {d.summary} ({', '.join(d.paths[:4])})" for d in n.deltas]
    text = "\n".join(parts)
    return text[:180_000]


def stack_deltas(inv: Inventory) -> list[Delta]:
    """Exact versions from the inventory, one entry per runtime set and package ecosystem."""
    deltas: list[Delta] = []
    if inv.runtimes:
        body = "; ".join(f"{r['what']} {r['version']} ({r['file']})" for r in inv.runtimes)
        files = list(dict.fromkeys(r["file"] for r in inv.runtimes))[:MAX_PATHS]
        deltas.append(
            Delta("add", "stack", "Runtimes and platform versions", body[:MAX_BODY], files)
        )
    declared = {d["name"].lower().split("[")[0] for d in inv.declared}
    by_type: dict[str, list[dict[str, str]]] = defaultdict(list)
    for p in inv.packages:
        by_type[p["type"]].append(p)
    for kind, pkgs in sorted(by_type.items()):
        direct = [p for p in pkgs if p["name"].lower() in declared] or pkgs
        listed = ", ".join(f"{p['name']} {p['version']}" for p in direct[:60])
        rest = len(pkgs) - len(direct)
        body = f"Installed and declared: {listed}." + (
            f" Plus {rest} indirect packages." if rest > 0 else ""
        )
        sources = list(dict.fromkeys(d["file"] for d in inv.declared))[:MAX_PATHS]
        label = {
            "python": "Python",
            "npm": "JavaScript (npm)",
            "github-action": "GitHub Actions",
        }.get(kind, kind)
        deltas.append(
            Delta(
                "add", "stack", f"{label} dependencies and exact versions", body[:MAX_BODY], sources
            )
        )
    if not inv.packages and inv.declared:
        by_file: dict[str, list[str]] = defaultdict(list)
        for d in inv.declared:
            by_file[d["file"]].append(f"{d['name']}{d['spec']}")
        for file, deps in by_file.items():
            deltas.append(
                Delta(
                    "add",
                    "stack",
                    f"Dependencies declared in {file}",
                    ", ".join(deps)[:MAX_BODY],
                    [file],
                )
            )
    if inv.vulnerabilities:
        body = "; ".join(
            f"{v['package']} {v['version']}: {', '.join(v['ids'][:5])}" for v in inv.vulnerabilities
        )
        deltas.append(
            Delta(
                "add",
                "pitfall",
                "Dependencies with known vulnerabilities (OSV)",
                body[:MAX_BODY],
                [],
            )
        )
    return deltas


# Sessions ------------------------------------------------------------------------------------


def _task(
    system: str,
    prompt: str,
    workdir: Path,
    policy: Policy,
    model: str | None,
    turns: int,
    meta: dict[str, str],
) -> AgentTask:
    return AgentTask(
        prompt=prompt,
        system_prompt=system,
        workdir=workdir,
        allowed_tools=READ_TOOLS,
        policy=policy,
        budget=Budget(max_usd=None, max_turns=turns),
        model=model,
        metadata=meta,
    )


def _journal_to(journal: Journal, prefix: str) -> Any:
    def record(event: Any) -> None:
        journal.append(f"{prefix}.{event.type}", event_to_dict(event))

    return record


def _read_paths(event: Any, root: Path) -> str | None:
    if not isinstance(event, ToolCallEvent) or event.tool.lower() != "read":
        return None
    raw = str(event.input.get("file_path") or event.input.get("path") or "")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


async def read_module(
    runtime: AgentRuntime,
    inv: Inventory,
    module: Module,
    *,
    workdir: Path,
    policy: Policy,
    model: str | None,
    journal: Journal,
) -> ModuleNotes:
    notes = ModuleNotes(module.name)
    started = time.monotonic()
    turns = min(200, 20 + 3 * len(module.files))
    task = _task(
        READER_SYSTEM,
        module_brief(inv, module),
        workdir,
        policy,
        model,
        turns,
        {"role": "scout", "module": module.name},
    )
    read: set[str] = set()

    def record(event: Any) -> None:
        journal.append(f"reader.{event.type}", {"module": module.name, **event_to_dict(event)})
        path = _read_paths(event, workdir)
        if path:
            read.add(path)

    try:
        out = await collect(runtime, task, on_event=record)
        raw = out.final_text
        notes.deltas = [d for d in parse(raw, limit=READER_DELTAS) if d.op == "add"]
        try:
            start = raw.index("{")
            notes.summary = str(
                json.loads(raw[start : raw.rindex("}") + 1]).get("summary", "")
            ).strip()
        except ValueError:
            notes.summary = ""
    except Exception as exc:  # one failed reader must not stop the scan
        notes.error = str(exc) or type(exc).__name__
    notes.files_read = sorted(read & set(module.files))
    notes.seconds = round(time.monotonic() - started, 1)
    return notes


def probe_delta(module: Module) -> Delta:
    """A data or assets folder as a layout entry, from the tools' probe alone."""
    where = f"{module.root}/" if module.root else "The repository root"
    noun = "data files" if module.kind == "data" else "images and other assets"
    summary = f"{where} holds {module.data_count} {noun}"
    used = list(module.probe.get("used_by") or [])
    paths = ([module.root] if module.root else []) + used
    return Delta(
        "add", "layout", summary[:190], describe_group(module)[:MAX_BODY], paths[:MAX_PATHS]
    )


def module_layout_delta(module: Module, summary: str) -> Delta | None:
    if not summary:
        return None
    folder = module.folder if module.folder != "(root)" else ""
    title = f"{module.name}: " + summary.split(". ")[0].rstrip(".")
    return Delta(
        "add",
        "layout",
        title[:190],
        summary[:MAX_BODY],
        [folder] if folder else module.files[:MAX_PATHS],
    )


async def deep_seed(
    db: Database,
    settings: Settings,
    runtime: AgentRuntime,
    slug: str,
    *,
    reader_model: str | None,
    synth_model: str | None,
    readers_at_once: int = READERS_AT_ONCE,
) -> DeepScanResult:
    with db.session() as session:
        project = store.get_project(session, slug)
        slug = project.slug
        checkout, agent = Path(project.checkout_path), Path(project.agent_path)
        branch = project.default_branch
        others = [
            Path(p)
            for other in store.list_projects(session)
            for p in (other.checkout_path, other.agent_path)
            if Path(p) not in (agent, checkout)
        ]
    result = DeepScanResult(slug)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    journal = Journal(settings.journal_dir / f"deepscan-{slug}-{stamp}.jsonl")
    result.journal = str(journal.path)
    total = time.monotonic()
    worktrees = WorktreeManager(
        settings.worktrees_dir,
        author_name=settings.git_author_name,
        author_email=settings.git_author_email,
    )
    try:
        clone = await asyncio.to_thread(worktrees.prepare_agent_clone, checkout, agent, branch)
    except Exception as exc:
        result.error = f"could not prepare PRAT's clone: {exc}"
        return result
    head = clone.default_head or ""

    # 1. Inventory.
    clock = time.monotonic()
    inv = await asyncio.to_thread(
        build_inventory, agent, head, installed=checkout, home=settings.home
    )
    result.timings["inventory"] = round(time.monotonic() - clock, 1)
    result.files, result.readable, result.modules = (
        len(inv.files),
        len(inv.readable),
        len(inv.modules),
    )
    kb = KnowledgeBase(settings.home, slug)
    kb.ensure()
    save_inventory(kb.root, inv)
    journal.append(
        "deepscan.inventory",
        {
            "files": result.files,
            "modules": result.modules,
            "tools": inv.tools,
            "timings": inv.timings,
        },
    )

    spec = _spec(agent, branch)
    policy = Policy(
        Autonomy.READ_ONLY,
        agent,
        protected=spec.protected_paths if spec else (),
        secret_paths=spec.secret_paths if spec else (),
        denied_roots=[*others, settings.home],
    )

    # 2. Read every module.
    clock = time.monotonic()
    gate = asyncio.Semaphore(max(1, readers_at_once))

    async def one(module: Module) -> ModuleNotes:
        async with gate:
            return await read_module(
                runtime,
                inv,
                module,
                workdir=agent,
                policy=policy,
                model=reader_model,
                journal=journal,
            )

    # Only code, config and docs are read by a model; data and assets were probed by tools.
    code_modules = [m for m in inv.modules if not m.data]
    notes = await asyncio.gather(*(one(m) for m in code_modules))
    result.timings["read"] = round(time.monotonic() - clock, 1)
    readable_paths = {f.path for f in inv.readable}
    result.files_read = len({p for n in notes for p in n.files_read} & readable_paths)
    result.reader_errors = [f"{n.module}: {n.error}" for n in notes if n.error]
    journal.append(
        "deepscan.read",
        {
            "modules": [
                {
                    "module": n.module,
                    "seconds": n.seconds,
                    "read": len(n.files_read),
                    "deltas": len(n.deltas),
                    "error": n.error,
                }
                for n in notes
            ]
        },
    )

    # 3. Synthesise.
    clock = time.monotonic()
    synth: list[Delta] = []
    try:
        out = await collect(
            runtime,
            _task(
                SYNTH_SYSTEM,
                synth_brief(inv, list(notes)),
                agent,
                policy,
                synth_model,
                60,
                {"role": "scout", "module": "(synthesis)"},
            ),
            on_event=_journal_to(journal, "synth"),
        )
        synth = [d for d in parse(out.final_text, limit=SYNTH_DELTAS) if d.op == "add"]
    except Exception as exc:
        result.reader_errors.append(f"synthesis: {exc}")
    result.timings["synthesis"] = round(time.monotonic() - clock, 1)

    # Apply: versions, module summaries, module notes, then the project-wide entries.
    deltas: list[Delta] = list(stack_deltas(inv))
    deltas += [probe_delta(m) for m in inv.modules if m.data]
    for module, n in zip(code_modules, notes, strict=True):
        layout = module_layout_delta(module, n.summary)
        if layout is not None:
            deltas.append(layout)
        deltas += n.deltas
    deltas += synth
    applied = curator.apply(
        kb, deltas, source=Source(None, None, f"deep scan, {stamp}"), author="scout"
    )
    result.added, result.confirmed = list(applied.added), list(applied.confirmed)
    result.held = list(applied.held)
    result.rejected = [f"{d.summary or d.op}: {reason}" for d, reason in applied.rejected]
    kb.write_index()
    result.timings["total"] = round(time.monotonic() - total, 1)
    read = f"{result.files_read} of {result.readable} files read"
    result.commit = kb.commit(f"Deep scan: {len(result.added)} entries from {read}")
    journal.append("deepscan.done", result.to_dict())
    report = json.dumps(result.to_dict(), indent=1) + "\n"
    (kb.root / SCAN_FILE).write_text(report, encoding="utf-8", newline="\n")
    kb.commit("Deep scan report")
    return result


def module_of(inv: Inventory, path: str) -> str:
    """The module a repository path belongs to (a file, or a folder a module covers)."""
    info = inv.file(path)
    if info is not None and info.module:
        return info.module
    clean = path.rstrip("/")
    for m in inv.modules:
        if m.folder == clean or posixpath.dirname(clean) == m.folder:
            return m.name
    return ""
