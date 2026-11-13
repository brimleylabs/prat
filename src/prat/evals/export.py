"""Export a finished card as an evaluation case.

The case holds the card (title, goal, acceptance criteria), the files of the commit its
first run started from, the journal of every run, and how each run went. Each run becomes
a scripted attempt that replays it: every shell command the builder asked for goes back
through the policy without being run (``dry``), then the files the run changed are written
as they were at the end of that run. Gates, the reviewers and the hidden check (if one is
added) then run on the replayed work for real, so a change to policy, gates or reviewers
shows up against the recorded outcome. The recorded verdicts become the scripted reviewer's.

An exported case has no hidden check until someone adds one: a ``[check]`` table in
``case.toml`` and the files it needs in ``hidden/``.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prat import store
from prat.config import Settings
from prat.db import Database, Run
from prat.domain import CardKind, CardStatus, RunStatus
from prat.evals.cases import CASE_FILE, CASE_NAME, CaseError, load_case
from prat.evals.runner import export_tree
from prat.git import Git, GitError
from prat.journal import read_journal
from prat.projects import ProjectFileError, load_spec

MAX_FILE_BYTES = 1024 * 1024
MAX_ATTEMPTS = 10
FINISHED = (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.BLOCKED, RunStatus.CANCELLED)


@dataclass
class ExportReport:
    path: Path
    attempts: int
    skipped_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _RecordedRun:
    run_id: int
    status: str
    reason: str
    base: str | None
    head: str | None
    journal: Path
    gates: dict[str, bool]
    critique: dict[str, Any]


def export_card(db: Database, settings: Settings, card_id: int, dest: Path) -> ExportReport:
    dest = dest.expanduser().resolve()
    if not CASE_NAME.match(dest.name.lower()):
        raise CaseError(f"{dest.name}: name the case folder with letters, digits and hyphens")
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise CaseError(f"{dest} already exists and is not empty")
    with db.session() as session:
        card = store.get_card(session, card_id)
        if card.kind == CardKind.SETUP:
            raise CaseError("a setup card writes the project file; it cannot be replayed")
        if card.status == CardStatus.RUNNING:
            raise CaseError(f"card {card_id} is running; export it when the run ends")
        runs = [run for run in card.runs if run.status in FINISHED]
        if not runs:
            raise CaseError(f"card {card_id} has no finished run")
        project = card.project
        agent_clone = Path(project.agent_path)
        title, goal, criteria = card.title, card.goal, card.acceptance_criteria
        complexity, slug = str(card.complexity), project.slug
        recorded = [_recorded(run, settings) for run in runs]
    report = ExportReport(dest, 0)
    if len(recorded) > MAX_ATTEMPTS:
        report.warnings.append(f"only the first {MAX_ATTEMPTS} of {len(recorded)} runs exported")
        recorded = recorded[:MAX_ATTEMPTS]
    base = recorded[0].base
    if base is None:
        raise CaseError(f"card {card_id}'s first run never prepared a worktree")
    if not Git(agent_clone).run("cat-file", "-e", f"{base}^{{commit}}", check=False).ok:
        raise CaseError(f"the commit card {card_id} started from is gone from {agent_clone}")

    dest.mkdir(parents=True, exist_ok=True)
    try:
        export_tree(agent_clone, base, dest / "repo")
        try:
            spec = load_spec(dest / "repo", default_name=slug)
        except ProjectFileError as exc:
            raise CaseError(str(exc)) from exc
        if not (dest / "repo" / ".prat" / "project.toml").is_file():
            report.warnings.append(
                "the starting commit has no .prat/project.toml: the case runs without gates"
            )
        if spec.workspace.database:
            report.warnings.append(
                "the project file declares databases: evaluations refuse to run it until "
                "the [workspace] section is removed from repo/.prat/project.toml"
            )
        previous = base
        for number, run in enumerate(recorded, start=1):
            steps: list[dict[str, Any]] = [
                {"op": "bash", "command": command, "dry": True}
                for command in _builder_commands(run.journal)
            ]
            if run.head and run.head != previous:
                steps += _file_steps(agent_clone, previous, run.head, report)
                previous = run.head
            steps.append({"op": "finish", "message": f"replayed run {run.run_id}"})
            attempt = dest / "attempts" / f"{number}.json"
            attempt.parent.mkdir(parents=True, exist_ok=True)
            attempt.write_text(json.dumps({"steps": steps}, indent=2) + "\n", encoding="utf-8")
            if run.journal.is_file():
                (dest / "journals").mkdir(exist_ok=True)
                shutil.copyfile(run.journal, dest / "journals" / f"run-{run.run_id}.jsonl")
        report.attempts = len(recorded)
        text = _case_toml(
            card_id=card_id,
            slug=slug,
            title=title,
            goal=goal,
            criteria=criteria,
            complexity=complexity,
            runs=recorded,
            skipped=report.skipped_files,
        )
        (dest / CASE_FILE).write_text(text, encoding="utf-8")
        load_case(dest)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return report


def _recorded(run: Run, settings: Settings) -> _RecordedRun:
    details = {str(step.name): dict(step.detail or {}) for step in run.steps}
    verify = details.get("verify", {})
    finalize = details.get("finalize", {})
    head = run.head_commit or verify.get("head_commit") or finalize.get("safety_commit")
    journal = (
        Path(run.journal_path) if run.journal_path else settings.journal_dir / f"run-{run.id}.jsonl"
    )
    return _RecordedRun(
        run_id=run.id,
        status=str(run.status),
        reason=run.reason or "",
        base=run.base_commit,
        head=str(head) if head else None,
        journal=journal,
        gates={
            str(gate.get("name")): bool(gate.get("passed")) for gate in verify.get("gates") or []
        },
        critique=details.get("critique", {}),
    )


def _builder_policy(journal: Path) -> list[dict[str, Any]]:
    if not journal.is_file():
        return []
    return [
        entry.data
        for entry in read_journal(journal)
        if entry.kind == "policy" and entry.data.get("agent") == "builder"
    ]


def _builder_commands(journal: Path) -> list[str]:
    commands: list[str] = []
    for decision in _builder_policy(journal):
        call = decision.get("call") or {}
        if call.get("kind") == "bash" and call.get("command"):
            commands.append(str(call["command"]))
    return commands


def _file_steps(repo: Path, old: str, new: str, report: ExportReport) -> list[dict[str, Any]]:
    """Steps that turn the files of ``old`` into those of ``new``."""
    git = Git(repo)
    raw = git.run_bytes("diff", "--name-status", "--no-renames", "-z", old, new)
    parts = raw.decode("utf-8", errors="surrogateescape").split("\0")
    steps: list[dict[str, Any]] = []
    for status, path in zip(parts[0::2], parts[1::2], strict=False):
        if not status or not path:
            continue
        if status.startswith("D"):
            steps.append({"op": "delete", "path": path})
            continue
        try:
            data = git.run_bytes("show", f"{new}:{path}")
            if len(data) > MAX_FILE_BYTES or b"\0" in data:
                raise ValueError("binary or too large")
            content = data.decode("utf-8")
        except (GitError, ValueError):
            if path not in report.skipped_files:
                report.skipped_files.append(path)
            continue
        steps.append({"op": "write", "path": path, "content": content})
    return steps


def toml_string(text: str) -> str:
    out: list[str] = []
    for ch in text:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _strings(values: list[str]) -> str:
    return "[" + ", ".join(toml_string(value) for value in values) + "]"


def _case_toml(
    *,
    card_id: int,
    slug: str,
    title: str,
    goal: str,
    criteria: str,
    complexity: str,
    runs: list[_RecordedRun],
    skipped: list[str],
) -> str:
    lines = [
        f"# Exported from card {card_id} of project {slug}.",
        "# To score the work, add a [check] table (command, timeout_seconds) and the files it",
        "# needs in hidden/. Without one, the case reports gates, verdicts and policy only.",
        f"title = {toml_string(title)}",
        f"goal = {toml_string(goal)}",
        f"acceptance_criteria = {toml_string(criteria)}",
        f"complexity = {toml_string(complexity)}",
        f"max_attempts = {len(runs)}",
    ]
    for run in runs:
        verdict = run.critique.get("verdict")
        if not verdict:
            continue
        lines += [
            "",
            "[[scripted_review]]",
            f"verdict = {toml_string(str(verdict))}",
            f"summary = {toml_string(str(run.critique.get('summary') or ''))}",
            f"fix_list = {_strings([str(item) for item in run.critique.get('fix_list') or []])}",
        ]
    lines += [
        "",
        "[recorded]",
        f"card_id = {card_id}",
        f"project = {toml_string(slug)}",
        f"exported_at = {toml_string(datetime.now(UTC).isoformat(timespec='seconds'))}",
        f"skipped_files = {_strings(skipped)}",
    ]
    for number, run in enumerate(runs, start=1):
        gates = ", ".join(
            f"{toml_string(name)} = {'true' if passed else 'false'}"
            for name, passed in run.gates.items()
        )
        denied = sum(1 for d in _builder_policy(run.journal) if not d.get("allowed"))
        lines += [
            "",
            "[[recorded.runs]]",
            f"attempt = {number}",
            f"run_id = {run.run_id}",
            f"run_status = {toml_string(run.status)}",
            f"reason = {toml_string(run.reason)}",
            f"verdict = {toml_string(str(run.critique.get('verdict') or ''))}",
            f"gates = {{{gates}}}" if gates else "gates = {}",
            f"denied = {denied}",
        ]
    return "\n".join(lines) + "\n"
