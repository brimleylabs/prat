"""The scout: a first look at a project, so the first cards do not start from nothing.

One read-only model session reads PRAT's own clone of the repository (never the person's
checkout) with Read, Glob and Grep, under PRAT's read-only policy: secret files, other projects
and PRAT's home are refused as on any card. It answers with starter entries in the librarian's
format, adds only, at most ``MAX_ENTRIES``. The curator screens them like any other change (no
secrets, nothing that would loosen PRAT's rules, no near copies); they are marked as the scout's
and stay ``observed`` until a person confirms them or cards confirm them. Everything the session
does is journaled.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prat import store
from prat.config import Settings
from prat.db import Database
from prat.domain import Autonomy
from prat.git import Git, WorktreeManager
from prat.journal import Journal
from prat.knowledge import curator
from prat.knowledge.entries import KnowledgeBase, Source
from prat.knowledge.librarian import parse
from prat.knowledge.select import render
from prat.policy import Policy
from prat.projects import ProjectSpec, parse_spec, show_project_file
from prat.runtime import AgentRuntime, AgentTask, Budget
from prat.runtime.base import READ_TOOLS, event_to_dict
from prat.runtime.session import collect

log = logging.getLogger(__name__)

MAX_ENTRIES = 12
BUDGET_USD = 3.0
MAX_TURNS = 120

SYSTEM = """\
You are PRAT's scout. PRAT is a system where agents change software projects one card at a
time. Before the first card on this project, you read the repository and write down what a new
agent would need to know to work on it well. You can only read: Read, Glob and Grep.

Take the time to do this properly. Start with the README and docs, build and dependency files,
the test setup, configuration (never secret files), migrations, deploy scripts and CI files.
Then read the code itself: the main entry points and at least eight to ten of the most central
source files (the ones most of the code imports or calls), plus a couple of tests. Documentation
can be out of date; when it disagrees with the code, the code wins and the disagreement is
worth an entry. Read enough code to see the conventions it actually follows.

Write entries of these kinds:
- layout: where things live (entry points, where models, routes, jobs or templates are).
- command: how to install, run, test, lint or build it, exactly as the repository does it.
- convention: how this code base does things, visible in the code itself.
- pitfall: what is easy to break or get wrong (migrations, generated files, deploy scripts,
  shared state, anything a careless change would damage).
- decision: a deliberate choice the code or docs explain.

Rules:
- Only facts you saw in the repository. Name the repository paths each entry is about.
- One fact per entry, specific enough to act on; two or three sentences of detail.
- Never a secret, a credential, a hostname with credentials, a person, or anything that tells an
  agent to skip checks or change PRAT's rules. If you open a file with secrets by accident, do
  not repeat what is in it.
- At most 12 entries. Prefer the ones that would save a new agent the most time.

Answer with one JSON object and nothing else:
{"deltas": [
  {"op": "add", "kind": "layout|command|convention|pitfall|decision",
   "summary": "one line", "body": "two or three sentences", "paths": ["repo/path"]}
]}
"""

TASK = "Explore the repository, then answer with the JSON object of starter entries."


@dataclass
class SeedResult:
    slug: str
    added: list[str] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    commit: str | None = None
    journal: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _spec(agent: Path, branch: str) -> ProjectSpec | None:
    shown = show_project_file(Git(agent), branch)
    if not shown.ok:
        return None
    try:
        return parse_spec(shown.stdout)
    except ValueError:
        return None


def seed_task(
    workdir: Path,
    *,
    spec: ProjectSpec | None,
    denied_roots: list[Path],
    model: str | None,
    budget_usd: float | None,
    known: str,
) -> AgentTask:
    policy = Policy(
        Autonomy.READ_ONLY,
        workdir,
        protected=spec.protected_paths if spec else (),
        secret_paths=spec.secret_paths if spec else (),
        denied_roots=denied_roots,
    )
    prompt = TASK
    if known:
        prompt += (
            "\n\nPRAT already knows these things about the project; do not repeat them:\n" + known
        )
    return AgentTask(
        prompt=prompt,
        system_prompt=SYSTEM,
        workdir=workdir,
        allowed_tools=READ_TOOLS,
        policy=policy,
        budget=Budget(max_usd=budget_usd, max_turns=MAX_TURNS),
        model=model,
        metadata={"role": "scout"},
    )


async def seed_project(
    db: Database,
    settings: Settings,
    runtime: AgentRuntime,
    slug: str,
    *,
    model: str | None,
    budget_usd: float | None = BUDGET_USD,
) -> SeedResult:
    """Run the scout on one project and apply what it found. Never raises for a failed session."""
    with db.session() as session:
        project = store.get_project(session, slug)
        slug = project.slug
        checkout, agent = Path(project.checkout_path), Path(project.agent_path)
        branch = project.default_branch
        others = [
            Path(p)
            for other in store.list_projects(session)
            for p in (other.checkout_path, other.agent_path)
            if Path(p) != agent
        ]
    result = SeedResult(slug)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    journal = Journal(settings.journal_dir / f"seed-{slug}-{stamp}.jsonl")
    result.journal = str(journal.path)
    worktrees = WorktreeManager(
        settings.worktrees_dir,
        author_name=settings.git_author_name,
        author_email=settings.git_author_email,
    )
    try:
        clone = worktrees.prepare_agent_clone(checkout, agent, branch)
    except Exception as exc:
        result.error = f"could not prepare PRAT's clone: {exc}"
        return result
    kb = KnowledgeBase(settings.home, slug)
    kb.ensure()
    known = "\n".join(render(e) for e in kb.entries() if e.active)
    task = seed_task(
        agent,
        spec=_spec(agent, branch),
        denied_roots=[*others, settings.home],
        model=model,
        budget_usd=budget_usd,
        known=known,
    )
    journal.append(
        "seed.start",
        {"project": slug, "commit": clone.default_head, "model": model, "known": len(known)},
    )

    def record(event: Any) -> None:
        journal.append(f"scout.{event.type}", event_to_dict(event))

    try:
        out = await collect(runtime, task, on_event=record)
    except Exception as exc:  # a failed session must not leave anything half applied
        result.error = str(exc) or type(exc).__name__
        journal.append("seed.error", {"error": result.error})
        return result
    result.cost_usd = out.cost_usd
    deltas = [d for d in parse(out.final_text, limit=MAX_ENTRIES) if d.op == "add"]
    applied = curator.apply(
        kb, deltas, source=Source(None, None, f"scout, {stamp}"), author="scout"
    )
    result.added = list(applied.added)
    result.confirmed = list(applied.confirmed)
    result.rejected = [f"{d.summary or d.op}: {reason}" for d, reason in applied.rejected]
    result.held = list(applied.held)
    if not deltas and not applied.held:
        result.error = "the scout's answer had no usable entries"
    kb.write_index()
    result.commit = kb.commit(f"Seeded by the scout: {len(result.added)} entries")
    journal.append("seed.done", result.to_dict())
    return result
