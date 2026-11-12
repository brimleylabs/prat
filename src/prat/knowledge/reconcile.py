"""Reconcile: a dream checks what PRAT tells agents against how the project is now.

Card runs only teach about the files they touched, so an entry about anything else can go out of
date without a card ever noticing: a version that was upgraded by hand, a script that was added,
a rule of PRAT's own that changed. Every Remember pass therefore takes a batch of active entries
and puts them to a checker with the evidence of today:

- the files each entry is about, as committed at the head of the default branch (or that they
  are gone);
- the tool inventory: declared and installed versions, runtimes, known vulnerabilities;
- the project file;
- PRAT's rules for agents on this project, found by running sample commands through the same
  policy a card would get, so the facts cannot drift from the code.

Each entry comes back true, wrong, refine (mostly right, a detail out of date: rewritten) or
unsure. Wrong is decisive here, because the evidence is the code as it is: the entry goes stale
and stops being given to agents. An entry a person wrote is never changed; a doubt about it is
written to the lint report instead.

What was checked, and at which commit, is kept in the knowledge cache. An entry is checked again
when a file it is about changes, or after ``RECHECK_AFTER``; entries never checked come first.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from prat.domain import Autonomy
from prat.git import Git
from prat.knowledge import curator
from prat.knowledge.curator import Delta
from prat.knowledge.entries import Entry, KnowledgeBase, Source, now_iso
from prat.knowledge.inventory import load_inventory
from prat.knowledge.select import render
from prat.policy import Decision, Policy, ToolCall
from prat.projects import (
    ProjectSpec,
    command_argv,
    command_text,
    show_project_file,
)
from prat.runtime import AgentRuntime, AgentTask, Budget, DoneEvent
from prat.runtime.base import CostEvent, TextEvent

CHECK_FILE = "checked.json"
RECHECK_AFTER = timedelta(days=14)
CHECK_PER_PASS = 120
BATCH_ENTRIES = 8
BATCH_FILES = 6
CHECKS_AT_ONCE = 4
MAX_FILE_CHARS = 8_000
MAX_INVENTORY_CHARS = 8_000
VERDICTS = ("true", "wrong", "refine", "unsure")

# Sample commands, judged by the project's real policy. The installed tool is created in a
# scratch worktree so "npx with a tool the project installed" is judged as it would be.
INSTALLED_TOOL = "tsc"
PROBES: tuple[tuple[str, str], ...] = (
    ("install a Python package from the index", "pip install requests"),
    ("install an npm package", "npm install left-pad"),
    ("reach the internet", "curl -s https://example.com"),
    ("check a server it started on this machine", "curl -s http://127.0.0.1:8000/health"),
    ("run a tool installed in node_modules through npx", f"npx {INSTALLED_TOOL} --noEmit"),
    ("run a package npx would download", "npx some-package"),
    ("run a script with Python", "python script.py"),
    ("edit a file in place with sed (macOS form)", "sed -i '' 's/a/b/' notes.txt"),
    ("write a file outside its worktree", "echo x > /tmp/out.txt"),
    ("change the project file", "echo x > .prat/project.toml"),
    ("push", "git push"),
)

SYSTEM = """\
You are PRAT's fact checker. PRAT keeps short entries about a software project and gives them
to the agents that change it, so an entry that is no longer true misleads every agent after it.
Check each entry below against the evidence, which is how things are now: the project's files at
the current commit, the tool inventory (declared and installed versions), the project file, and
PRAT's rules for agents on this project.

For each entry answer one verdict:
- "true": the evidence supports it, or it is about something the evidence shows unchanged.
- "wrong": the evidence shows it is no longer true. Quote or name the evidence.
- "refine": mostly right, but a detail is out of date (a version, a path, a command, a count).
  Give the corrected summary (one line) and body (two or three sentences).
- "unsure": the evidence does not cover it. Never answer wrong only because the evidence is
  silent.
Statements about what agents may or may not run must match "PRAT's rules now" exactly.

An entry is advice to the next agent, so judge the advice too, not only whether each sentence
can be read as true:
- Advice that PRAT's rules now make wrong or needlessly costly is "refine" (or "wrong" if
  nothing of it holds). Example: "ask for higher autonomy to install packages" when the
  project's setup command, which the rules allow, does the install.
- An entry that tells what happened on a past card ("this was denied in card 2") is history,
  not knowledge. Refine it to the fact that still holds today, or answer wrong if none does.
- Do not refine an entry only to reword it or fix punctuation: answer true.

Answer with one JSON object and nothing else:
{"checks": [
  {"id": "k0123456789", "verdict": "true"},
  {"id": "k0123456789", "verdict": "wrong", "evidence": "what shows it"},
  {"id": "k0123456789", "verdict": "refine", "evidence": "what changed",
   "summary": "...", "body": "..."},
  {"id": "k0123456789", "verdict": "unsure"}
]}"""


@dataclass
class Check:
    id: str
    verdict: str
    evidence: str = ""
    summary: str = ""
    body: str = ""


@dataclass
class ReconcileReport:
    checked: int = 0
    true: int = 0
    wrong: int = 0
    refined: int = 0
    unsure: int = 0
    doubted_person: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


# State -----------------------------------------------------------------------------------------


def _load(kb: KnowledgeBase) -> dict[str, dict[str, str]]:
    try:
        value = json.loads((kb.cache_dir() / CHECK_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return (
        {k: v for k, v in value.items() if isinstance(v, dict)} if isinstance(value, dict) else {}
    )


def _save(kb: KnowledgeBase, state: dict[str, dict[str, str]]) -> None:
    (kb.cache_dir() / CHECK_FILE).write_text(json.dumps(state), encoding="utf-8")


def _parse_time(stamp: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def due(
    kb: KnowledgeBase, git: Git, head: str, now: datetime, limit: int = CHECK_PER_PASS
) -> list[Entry]:
    """Active entries to check this pass: never checked first, then those whose files changed
    since they were checked, then those checked longest ago (past ``RECHECK_AFTER``)."""
    state = _load(kb)
    changed_since: dict[str, set[str] | None] = {}

    def changed(commit: str) -> set[str] | None:
        if commit not in changed_since:
            diff = git.run("diff", "--name-only", f"{commit}..{head}", check=False)
            changed_since[commit] = set(diff.stdout.split()) if diff.ok else None
        return changed_since[commit]

    ranked: list[tuple[int, str, Entry]] = []
    for entry in kb.entries():
        if not entry.active:
            continue
        seen = state.get(entry.id)
        if seen is None:
            ranked.append((0, "", entry))
            continue
        commit, stamp = seen.get("commit", ""), seen.get("at", "")
        if commit != head and entry.paths:
            files = changed(commit)
            if files is None or any(
                f == p or f.startswith(p.rstrip("/") + "/") for p in entry.paths for f in files
            ):
                ranked.append((1, stamp, entry))
                continue
        when = _parse_time(stamp)
        if when is None or now - when > RECHECK_AFTER:
            ranked.append((2, stamp, entry))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [entry for _, _, entry in ranked[:limit]]


# Evidence --------------------------------------------------------------------------------------


def prat_rules(spec: ProjectSpec, autonomy: Autonomy) -> str:
    """What an agent on this project may run, judged by the real policy on sample commands."""
    with tempfile.TemporaryDirectory(prefix="prat-rules-") as scratch:
        work = Path(scratch) / "worktree"
        tools = work / "node_modules" / ".bin"
        tools.mkdir(parents=True)
        (tools / INSTALLED_TOOL).write_text("")
        commands = [command_argv(c) for c in spec.commands.defined().values()]
        policy = Policy(
            autonomy,
            work,
            protected=spec.protected_paths,
            secret_paths=spec.secret_paths,
            project_commands=commands,
        )
        lines = [f"Autonomy for agents on this project: {autonomy}."]
        probes = [*PROBES]
        probes += [
            (f"the project's {name} command", command_text(c))
            for name, c in spec.commands.defined().items()
        ]
        for what, command in probes:
            decision: Decision = policy.decide(ToolCall.bash(command))
            verdict = "allowed" if decision.allowed else f"denied ({decision.reason})"
            lines.append(f"- {what}, e.g. `{command}`: {verdict}")
    return "\n".join(lines)


def _file_text(git: Git, head: str, path: str) -> str:
    shown = git.run("show", f"{head}:{path}", check=False)
    if shown.ok:
        text = shown.stdout
        if len(text) > MAX_FILE_CHARS:
            text = (
                text[:MAX_FILE_CHARS]
                + f"\n[... {len(shown.stdout) - MAX_FILE_CHARS} more characters]"
            )
        return text
    listed = git.run("ls-tree", "--name-only", f"{head}:{path.rstrip('/')}", check=False)
    if listed.ok and listed.stdout.strip():
        names = listed.stdout.split()
        return "(a folder) " + ", ".join(names[:80])
    return "(does not exist at the current commit)"


def _batches(entries: Sequence[Entry]) -> list[list[Entry]]:
    """Entries grouped so each batch shares a few files: one look at a file checks them all."""
    ordered = sorted(entries, key=lambda e: (e.paths[0] if e.paths else "~", e.kind))
    batches: list[list[Entry]] = []
    current: list[Entry] = []
    files: set[str] = set()
    for entry in ordered:
        mine = set(entry.paths[:3])
        if current and (len(current) >= BATCH_ENTRIES or len(files | mine) > BATCH_FILES):
            batches.append(current)
            current, files = [], set()
        current.append(entry)
        files |= mine
    if current:
        batches.append(current)
    return batches


def prompt(batch: Sequence[Entry], git: Git, head: str, shared: str) -> str:
    paths = list(dict.fromkeys(p for e in batch for p in e.paths[:3]))
    parts = [shared, "# The project's files now"]
    for path in paths:
        parts.append(f"## {path}\n{_file_text(git, head, path)}")
    if not paths:
        parts.append("(these entries name no files)")
    parts.append("# Entries to check")
    parts += [f"{render(e)} [id {e.id}]" for e in batch]
    return "\n\n".join(parts)


def shared_evidence(kb: KnowledgeBase, git: Git, head: str, rules: str) -> str:
    project_file = show_project_file(git, head)
    inventory = load_inventory(kb.root)
    parts = [
        f"# Current commit {head[:12]}",
        "# PRAT's rules now\n" + rules,
        "# Project file\n" + (project_file.stdout if project_file.ok else "(none)"),
    ]
    if inventory is not None:
        note = "" if inventory.commit == head else f" (taken at {inventory.commit[:12]})"
        parts.append(f"# Tool inventory{note}\n" + inventory.summary()[:MAX_INVENTORY_CHARS])
    return "\n\n".join(parts)


# Asking ----------------------------------------------------------------------------------------

JSON_OBJECT = re.compile(r"\{.*\}", re.S)


def parse(text: str, ids: set[str]) -> list[Check]:
    found = JSON_OBJECT.search(text or "")
    if not found:
        return []
    try:
        raw = json.loads(found.group(0))
    except ValueError:
        return []
    items = raw.get("checks") if isinstance(raw, dict) else None
    checks: list[Check] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        check = Check(
            id=str(item.get("id", "")),
            verdict=str(item.get("verdict", "")).lower(),
            evidence=str(item.get("evidence", "")).strip(),
            summary=" ".join(str(item.get("summary", "")).split()),
            body=str(item.get("body", "")).strip(),
        )
        if check.id in ids and check.verdict in VERDICTS:
            if check.verdict == "refine" and not check.summary:
                check.verdict = "unsure"
            checks.append(check)
    return checks


def _deny_everything(call: ToolCall) -> Decision:
    return Decision.deny("the checker has no tools")


async def _ask(
    runtime: AgentRuntime, text: str, workdir: Path, model: str | None
) -> tuple[str, float, str | None]:
    task = AgentTask(
        prompt=text,
        system_prompt=SYSTEM,
        workdir=workdir,
        allowed_tools=frozenset(),
        policy=_deny_everything,
        budget=Budget(max_usd=0.75, max_turns=1),
        model=model,
        metadata={"role": "checker"},
    )
    texts: list[str] = []
    final, cost = "", 0.0
    try:
        async for event in runtime.run(task):
            if isinstance(event, TextEvent):
                texts.append(event.text)
            elif isinstance(event, CostEvent):
                cost += event.usd
            elif isinstance(event, DoneEvent):
                final = event.message or ""
                break
    except Exception as exc:  # a failed check must not stop the dream
        return "", cost, str(exc) or type(exc).__name__
    return final or "\n".join(texts), cost, None


# The pass --------------------------------------------------------------------------------------


async def reconcile(
    kb: KnowledgeBase,
    agent: Path,
    head: str,
    runtime: AgentRuntime,
    *,
    spec: ProjectSpec,
    autonomy: Autonomy,
    model: str | None,
    now: datetime | None = None,
    limit: int = CHECK_PER_PASS,
    should_stop: Callable[[], bool] = lambda: False,
) -> ReconcileReport:
    """Check a batch of entries against the project as it is at ``head``. The caller commits."""
    when = now or datetime.now(UTC)
    report = ReconcileReport()
    git = Git(agent)
    entries = due(kb, git, head, when, limit)
    if not entries:
        return report
    shared = shared_evidence(kb, git, head, prat_rules(spec, autonomy))
    gate = asyncio.Semaphore(CHECKS_AT_ONCE)

    async def one(batch: list[Entry]) -> tuple[list[Entry], list[Check], str | None]:
        async with gate:
            if should_stop():
                return batch, [], None
            text = prompt(batch, git, head, shared)
            answer, cost, error = await _ask(runtime, text, kb.root, model)
            report.cost_usd += cost
            return batch, parse(answer, {e.id for e in batch}), error

    results = await asyncio.gather(*(one(b) for b in _batches(entries)))
    state = _load(kb)
    stamp = now_iso()
    source = Source(None, None, f"checked against {head[:12]}")
    for batch, checks, error in results:
        if error:
            report.problems.append(f"check: {error}")
        by_id = {e.id: e for e in batch}
        for check in checks:
            entry = by_id[check.id]
            report.checked += 1
            state[entry.id] = {"commit": head, "at": stamp, "verdict": check.verdict}
            if check.verdict == "true":
                report.true += 1
                entry.last_verified = stamp
                kb.save(entry)
            elif check.verdict == "unsure":
                report.unsure += 1
            elif entry.author == "person":
                report.doubted_person.append(
                    f"{entry.id} ({entry.summary!r}) may be out of date: {check.evidence[:300]}"
                )
            elif check.verdict == "wrong":
                applied = curator.apply(
                    kb,
                    [Delta(op="wrong", id=entry.id, evidence=check.evidence or "checked")],
                    source=source,
                    author="checker",
                    decisive=True,
                )
                report.wrong += len(applied.reported)
            else:
                applied = curator.apply(
                    kb,
                    [
                        Delta(
                            op="refine",
                            id=entry.id,
                            kind=entry.kind,
                            summary=check.summary,
                            body=check.body,
                            paths=list(entry.paths),
                        )
                    ],
                    source=source,
                    author="checker",
                )
                for _old, new in applied.refined:
                    report.refined += 1
                    state[new] = {"commit": head, "at": stamp, "verdict": "true"}
    _save(kb, state)
    return report
