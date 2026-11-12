"""Keeping project knowledge true: the Remember phase of a dream.

For every project, in order:

1. **Ingest**: each card run that finished since the last pass is gathered into evidence; the
   librarian proposes changes for those with something to teach and the curator applies them.
   A run whose card was still open (in review, say) is looked at again once the card settles:
   approved or cancelled, the outcome is new evidence, and the librarian confirms, refines or
   reports wrong what was learned while it was open.
2. **Feedback**: entries an agent was given on a card that then landed on its first run get
   ``helped + 1``. (Entries that misled an agent are reported wrong by the librarian.)
3. **Verify**: landed changes since the last pass are read from PRAT's clone. Renamed files
   are followed; an entry whose files are all gone becomes ``stale``. The code map and a deep
   scan's inventory (files, modules, relationships, versions) are rebuilt with tools.
4. **Check**: a batch of active entries is put to a checker with the project as it is now (its
   files, versions, project file and PRAT's rules for agents); what is no longer true goes
   stale and what is out of date is rewritten. See :mod:`prat.knowledge.reconcile`.
5. **Consolidate**: entries that say the same thing are merged (the newer is superseded, never
   deleted); entries stale for 30 days are retired. Pairs that only look alike (same kind, same
   files, some shared words) are put to the librarian, a batch per pass; a pair it calls
   distinct is never asked about again.
6. **Lint**: contradictions, entries that do more harm than good, entries a person wrote that
   the checker doubts, and files changed often with nothing known about them are written to
   ``LINT.md``.

Everything ends in one commit of the project's knowledge repository.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from prat.db import Card, Comment, Database, Project, Run
from prat.db.models import AppSetting
from prat.domain import Autonomy, CardStatus, RunStatus, StepName, lowest_autonomy
from prat.git import Git
from prat.journal import read_journal
from prat.knowledge import codemap, curator
from prat.knowledge.entries import (
    MAX_BODY,
    MAX_PATHS,
    MAX_SUMMARY,
    Entry,
    KnowledgeBase,
    Source,
    now_iso,
)
from prat.knowledge.inventory import build_inventory, load_inventory, save_inventory
from prat.knowledge.librarian import (
    MAX_DELTAS,
    SETTLED_DELTAS,
    CardEvidence,
    SameAnswer,
    propose,
    same_fact,
)
from prat.knowledge.reconcile import reconcile
from prat.knowledge.seed import _spec
from prat.projects import ProjectSpec
from prat.runtime import AgentRuntime

log = logging.getLogger(__name__)

WATERMARK_KEY = "knowledge.watermark"
# Runs read while their card was still open, per project: read again once the card settles.
OPEN_KEY = "knowledge.open_runs"
FINISHED = (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.BLOCKED, RunStatus.CANCELLED)
SETTLED = (CardStatus.DONE, CardStatus.CANCELLED)
VERIFY_AFTER = timedelta(days=30)
RETIRE_AFTER = timedelta(days=30)
HOT_TOUCHES = 3
MAX_RUNS_PER_PASS = 40
# Pairs this alike (and below the curator's own threshold) are asked about, a batch a pass.
NEAR_THRESHOLD = 0.2
CROSS_KIND_THRESHOLD = 0.35
MAX_PAIRS_PER_PASS = 30
PAIRS_AT_ONCE = 4
# Entries shown to the librarian with a card. A settled card is checked against more of them:
# what its agents were given already fills the usual dozen, and the landing can disprove any
# entry about the files it changed.
RELATED = 12
SETTLED_RELATED = 30


@dataclass
class ProjectReport:
    slug: str
    ingested: int = 0
    settled: int = 0
    asked: int = 0
    judged: int = 0
    # Entries checked against the project as it is, and what came of it.
    checked: int = 0
    check_wrong: int = 0
    check_refined: int = 0
    check_unsure: int = 0
    changes: list[str] = field(default_factory=list)
    helped: int = 0
    renamed: int = 0
    stale: int = 0
    verified: int = 0
    merged: int = 0
    retired: int = 0
    lint: list[str] = field(default_factory=list)
    map_files: int = 0
    inventory_refreshed: bool = False
    commit: str | None = None
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class RememberReport:
    projects: list[ProjectReport] = field(default_factory=list)
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"projects": [p.to_dict() for p in self.projects], "cost_usd": self.cost_usd}


# Watermarks ----------------------------------------------------------------------------------


def _watermarks(db: Database) -> dict[str, int]:
    with db.session() as session:
        row = session.get(AppSetting, WATERMARK_KEY)
        value = row.value if row is not None else {}
    return {str(k): int(v) for k, v in (value or {}).items()} if isinstance(value, dict) else {}


def _save_watermark(db: Database, slug: str, run_id: int) -> None:
    with db.session() as session:
        row = session.get(AppSetting, WATERMARK_KEY)
        value = dict(row.value or {}) if row is not None and isinstance(row.value, dict) else {}
        value[slug] = run_id
        if row is None:
            session.add(AppSetting(key=WATERMARK_KEY, value=value))
        else:
            row.value = value


def _open_runs(db: Database) -> dict[str, list[int]]:
    with db.session() as session:
        row = session.get(AppSetting, OPEN_KEY)
        value = row.value if row is not None else {}
    if not isinstance(value, dict):
        return {}
    return {str(k): [int(i) for i in v] for k, v in value.items() if isinstance(v, list)}


def _save_open_runs(db: Database, slug: str, run_ids: list[int]) -> None:
    with db.session() as session:
        row = session.get(AppSetting, OPEN_KEY)
        value = dict(row.value or {}) if row is not None and isinstance(row.value, dict) else {}
        value[slug] = sorted(set(run_ids))
        if row is None:
            session.add(AppSetting(key=OPEN_KEY, value=value))
        else:
            row.value = value


# Evidence --------------------------------------------------------------------------------------


def _changed_files(agent: Path, run: Run) -> list[str]:
    if not run.base_commit or not run.head_commit or not agent.exists():
        return []
    result = Git(agent).run(
        "diff", "--name-only", f"{run.base_commit}..{run.head_commit}", check=False
    )
    return [line for line in result.stdout.splitlines() if line.strip()] if result.ok else []


def _outcome(card: Card, run: Run, runs_before: int) -> str:
    if run.status == RunStatus.SUCCEEDED and card.status == CardStatus.DONE:
        return "landed first time" if runs_before == 0 else f"landed after {runs_before + 1} runs"
    if card.status == CardStatus.REWORK:
        return "sent back for rework"
    if run.status == RunStatus.SUCCEEDED:
        return f"reached {card.status}"
    return f"{run.status}: {run.reason or ''}".strip(": ")


def gather(session: Any, run: Run, *, settled: bool = False) -> tuple[CardEvidence, list[str]]:
    """Evidence for one finished run, and the entry ids its agents were given. ``settled``
    marks a second look at a run read while its card was open."""
    card: Card = run.card
    agent = Path(card.project.agent_path)
    runs_before = sum(1 for r in card.runs if r.id < run.id)
    evidence = CardEvidence(
        card_id=card.id,
        run_id=run.id,
        title=card.title,
        goal=card.goal or "",
        criteria=card.acceptance_criteria or "",
        outcome=_outcome(card, run, runs_before),
        changed_files=_changed_files(agent, run),
        settled=settled,
    )
    provided: list[str] = []
    for step in run.steps:
        detail = step.detail or {}
        provided += [str(i) for i in detail.get("knowledge") or []]
        if step.name == StepName.VERIFY:
            for gate in detail.get("gates") or []:
                if not isinstance(gate, dict):
                    continue
                if gate.get("passed"):
                    evidence.checks_passed.append(f"{gate.get('name')}: {gate.get('command')}")
                else:
                    tail = str(gate.get("output_tail") or "").strip().splitlines()[-1:]
                    evidence.gate_failures.append(f"{gate.get('name')}: {' '.join(tail)}")
    if run.feedback:
        evidence.feedback.append(run.feedback.strip()[:1500])
    comments = session.scalars(
        select(Comment).where(Comment.card_id == card.id).order_by(Comment.id)
    )
    for comment in comments:
        data = comment.data or {}
        if comment.kind == "verdict" and comment.run_id == run.id:
            evidence.verdict = str(data.get("verdict") or "")
            evidence.fix_list = [str(x) for x in data.get("fix_list") or []]
        elif str(comment.author) == "human":
            evidence.comments.append(comment.body.strip()[:800])
    if run.journal_path:
        for entry in read_journal(Path(run.journal_path)):
            if entry.kind == "policy" and not entry.data.get("allowed", True):
                evidence.denials.append(str(entry.data.get("reason", ""))[:200])
    return evidence, list(dict.fromkeys(provided))


def _fold_runs_of_a_card(work: list[tuple[int, int, CardEvidence, list[str], bool]]) -> set[int]:
    """Several runs of one card read in the same pass (a card reworked a few times) are put to
    the librarian once, as its latest run with what the earlier runs showed folded in, so one
    lesson is not written down once per run. Returns the earlier runs' ids, which are still
    counted and watermarked but not asked about."""
    latest: dict[int, int] = {}
    for index, (_run_id, card_id, evidence, _given, _open) in enumerate(work):
        if not evidence.settled:
            latest[card_id] = index
    folded: set[int] = set()
    for index, (run_id, card_id, evidence, _given, _open) in enumerate(work):
        last = latest.get(card_id)
        if evidence.settled or last is None or last == index:
            continue
        target = work[last][2]
        for name in ("fix_list", "feedback", "comments", "denials", "gate_failures"):
            merged = [*getattr(evidence, name), *getattr(target, name)]
            setattr(target, name, list(dict.fromkeys(merged)))
        folded.add(run_id)
    return folded


def related_entries(
    entries: list[Entry],
    files: list[str],
    limit: int = 12,
    *,
    card_id: int | None = None,
    provided: Sequence[str] = (),
) -> list[Entry]:
    """Active entries about the given files (or their folders), what the card's agents were
    given, and what earlier runs of the same card taught: those first, then most confirmed.
    A run that changed nothing (blocked, say) still sees what its card already taught, so the
    librarian refines instead of adding a near copy."""
    given = set(provided)
    found = []
    for entry in entries:
        if not entry.active:
            continue
        from_card = card_id is not None and any(s.card == card_id for s in entry.sources)
        if (
            from_card
            or entry.id in given
            or any(p == f or f.startswith(p.rstrip("/") + "/") for p in entry.paths for f in files)
            or not entry.paths
        ):
            found.append((from_card or entry.id in given, entry))
    found.sort(key=lambda pair: (not pair[0], not pair[1].confirmed, pair[1].hurt - pair[1].helped))
    return [entry for _, entry in found[:limit]]


# Verify, consolidate, lint ---------------------------------------------------------------


def _state(kb: KnowledgeBase) -> dict[str, Any]:
    try:
        value = json.loads((kb.cache_dir() / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_state(kb: KnowledgeBase, state: dict[str, Any]) -> None:
    (kb.cache_dir() / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _exists(git: Git, commit: str, path: str) -> bool:
    return git.run("cat-file", "-e", f"{commit}:{path}", check=False).ok


def verify(kb: KnowledgeBase, agent: Path, head: str, report: ProjectReport, now: datetime) -> None:
    git = Git(agent)
    state = _state(kb)
    previous = state.get("verified_commit")
    renames: dict[str, str] = {}
    deleted: set[str] = set()
    if (
        previous
        and previous != head
        and git.run("cat-file", "-e", f"{previous}^{{commit}}", check=False).ok
    ):
        diff = git.run("diff", "--name-status", "-M", f"{previous}..{head}", check=False)
        for line in diff.stdout.splitlines() if diff.ok else []:
            parts = line.split("\t")
            if parts[0].startswith("R") and len(parts) == 3:
                renames[parts[1]] = parts[2]
            elif parts[0] == "D" and len(parts) == 2:
                deleted.add(parts[1])
    for entry in kb.entries():
        if not entry.active or (entry.author == "person" and not entry.paths):
            continue
        changed = False
        if any(p in renames for p in entry.paths):
            entry.paths = [renames.get(p, p) for p in entry.paths]
            report.renamed += 1
            changed = True
        stamp = _parse(entry.last_verified)
        due = stamp is None or now - stamp > VERIFY_AFTER
        if entry.paths and (due or any(p in deleted for p in entry.paths) or changed):
            alive = [p for p in entry.paths if _exists(git, head, p)]
            if not alive:
                entry.status = "stale"
                entry.invalid_at = now_iso()
                report.stale += 1
            else:
                entry.last_verified = now_iso()
                report.verified += 1
            changed = True
        elif not entry.paths and due:
            entry.last_verified = now_iso()
            changed = True
        if changed:
            kb.save(entry)
    state["verified_commit"] = head
    _save_state(kb, state)


def _parse(stamp: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _fold(kb: KnowledgeBase, *, keep: Entry, fold: Entry) -> None:
    """``fold`` says what ``keep`` says: it is superseded (never deleted) and ``keep`` takes its
    sources and credit."""
    keep.confidence = "confirmed"
    keep.sources += fold.sources
    keep.helped += fold.helped
    fold.status = "superseded"
    fold.superseded_by = keep.id
    fold.invalid_at = now_iso()
    kb.save(keep)
    kb.save(fold)


# File names, paths, commands in backticks, identifiers and versions: what an agent acts on.
SPECIFIC = re.compile(r"`[^`]{2,80}`|\b[\w-]+(?:[./][\w-]+)+\b|\b\w+_\w+\b|\b\d+(?:\.\d+)+\b")


def specifics(text: str) -> set[str]:
    return {token.strip("`").lower() for token in SPECIFIC.findall(text)}


def specifics_missing(text: str, entries: Sequence[Entry]) -> list[str]:
    """The specific items (files, commands, identifiers, versions) the entries name in their
    summaries that ``text`` no longer mentions. A merge that loses one lost a fact."""
    have = specifics(text)
    lowered = text.lower()
    missing = []
    for entry in entries:
        for item in sorted(specifics(entry.summary)):
            if item not in have and item not in lowered:
                missing.append(item)
    return list(dict.fromkeys(missing))


def _pair_key(a: Entry, b: Entry) -> str:
    return "|".join(sorted((a.id, b.id)))


def near_duplicates(kb: KnowledgeBase) -> list[tuple[Entry, Entry]]:
    """Pairs of active entries that may say the same thing but are not alike enough for the
    curator to merge on its own: overlapping files and shared words (more of them when the two
    are of different kinds, a command and a pitfall stating one fact, say). Pairs already judged
    distinct are left out. Most alike first."""
    distinct = set(_state(kb).get("distinct") or [])
    entries = [e for e in kb.entries() if e.active and e.paths]
    pairs: list[tuple[float, Entry, Entry]] = []
    for i, first in enumerate(entries):
        for second in entries[i + 1 :]:
            if _pair_key(first, second) in distinct:
                continue
            if first.author == "person" and second.author == "person":
                continue
            if not curator.paths_overlap(first.paths, second.paths):
                continue
            value = curator.similarity(first, second.summary, second.body)
            floor = NEAR_THRESHOLD if first.kind == second.kind else CROSS_KIND_THRESHOLD
            if floor <= value < curator.SAME_THRESHOLD:
                pairs.append((value, first, second))
    pairs.sort(key=lambda item: -item[0])
    return [(a, b) for _, a, b in pairs]


async def judge_duplicates(
    kb: KnowledgeBase,
    runtime: AgentRuntime,
    report: ProjectReport,
    *,
    model: str | None,
    should_stop: Callable[[], bool] = lambda: False,
) -> float:
    """Ask the librarian about a batch of near duplicates (a few at once) and fold those it
    calls the same into the entry that says more. Returns the cost."""
    cost = 0.0
    state = _state(kb)
    distinct = list(state.get("distinct") or [])
    pairs = near_duplicates(kb)[:MAX_PAIRS_PER_PASS]
    gate = asyncio.Semaphore(PAIRS_AT_ONCE)

    async def ask(first: Entry, second: Entry) -> SameAnswer | None:
        async with gate:
            if should_stop():
                return None
            return await same_fact(runtime, first, second, workdir=kb.root, model=model)

    answers = await asyncio.gather(*(ask(a, b) for a, b in pairs))
    # An entry changes at most once a pass: a second answer was written against its old text.
    touched: set[str] = set()
    for (first, second), answer in zip(pairs, answers, strict=True):
        if answer is None:
            continue
        cost += answer.cost_usd
        if first.id in touched or second.id in touched:
            continue
        if answer.error:
            report.problems.append(f"judging {first.id} and {second.id}: {answer.error}")
            continue
        report.judged += 1
        if not answer.same:
            distinct.append(_pair_key(first, second))
            continue
        keep, fold = (second, first) if answer.keep == "B" else (first, second)
        if fold.author == "person":
            keep, fold = fold, keep  # a person's entry is never folded away
        summary, body = answer.summary[:MAX_SUMMARY], answer.body[:MAX_BODY]
        rewrite = (
            keep.author != "person"
            and bool(summary)
            and curator.screen(f"{summary}\n{body}") is None
        )
        if rewrite:
            # New text must itself carry every fact: the file list alone does not say what
            # the other entry said about the file.
            result_text = f"{summary}\n{body}"
        else:
            # Folding as it stands: the surviving entry keeps both file lists.
            result_text = f"{keep.summary}\n{keep.body}\n" + " ".join([*keep.paths, *fold.paths])
        lost = specifics_missing(result_text, [first, second] if rewrite else [fold])
        if lost:
            # The surviving text would drop a file, command or value one of them names: they
            # are not one fact after all.
            distinct.append(_pair_key(first, second))
            report.changes.append(f"kept {fold.id} apart from {keep.id}: {', '.join(lost[:3])}")
            continue
        if rewrite:
            # One entry saying what both said.
            keep.summary, keep.body = summary, body
            keep.paths = list(dict.fromkeys([*keep.paths, *fold.paths]))[:MAX_PATHS]
        _fold(kb, keep=keep, fold=fold)
        touched.update((keep.id, fold.id))
        report.merged += 1
        report.changes.append(f"merged {fold.id} into {keep.id}")
    state = _state(kb)
    state["distinct"] = distinct[-2000:]
    _save_state(kb, state)
    return cost


def consolidate(kb: KnowledgeBase, report: ProjectReport, now: datetime) -> None:
    entries = [e for e in kb.entries() if e.active]
    entries.sort(key=lambda e: (e.author != "person", not e.confirmed, e.created))
    keepers: list[Entry] = []
    for entry in entries:
        same = curator.find_same(keepers, entry.summary, entry.body, entry.paths)
        if same is None:
            keepers.append(entry)
            continue
        if entry.author == "person":
            keepers.append(entry)  # never folded into an entry a person did not write
            continue
        _fold(kb, keep=same, fold=entry)
        report.merged += 1
    for entry in kb.entries():
        stale_since = _parse(entry.invalid_at)
        if entry.status == "stale" and stale_since and now - stale_since > RETIRE_AFTER:
            entry.status = "retired"
            kb.save(entry)
            report.retired += 1


def lint(
    kb: KnowledgeBase,
    touches: Counter[str],
    report: ProjectReport,
    doubts: Sequence[str] = (),
) -> None:
    """``doubts`` are entries a person wrote that the checker found out of date: PRAT never
    changes those, so they are reported here."""
    entries = kb.entries()
    by_id = {e.id: e for e in entries}
    issues: list[str] = [f"written by a person, {doubt}" for doubt in doubts]
    for entry in entries:
        if not entry.active:
            continue
        for link in entry.links:
            other = by_id.get(link.to)
            if link.relation == "contradicts" and other is not None and other.active:
                issues.append(
                    f"{entry.id} contradicts {other.id}: {entry.summary!r} vs {other.summary!r}"
                )
        if entry.hurt >= 2 and entry.hurt > entry.helped:
            issues.append(f"{entry.id} has misled agents {entry.hurt} times: {entry.summary!r}")
    covered = [p for e in entries if e.active for p in e.paths]
    for path, count in touches.most_common(20):
        if count < HOT_TOUCHES:
            break
        if not any(path == p or path.startswith(p.rstrip("/") + "/") for p in covered):
            issues.append(f"{path} changed in {count} cards and nothing is known about it")
    report.lint = issues
    lines = ["# Knowledge lint", "", "Written by PRAT's Remember phase.", ""]
    lines += [f"- {issue}" for issue in issues] or ["- Nothing to report."]
    kb.ensure()
    (kb.root / "LINT.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _touches(kb: KnowledgeBase, files: list[str]) -> Counter[str]:
    state = _state(kb)
    counts: Counter[str] = Counter(state.get("touches") or {})
    counts.update(files)
    state["touches"] = dict(counts.most_common(500))
    _save_state(kb, state)
    return counts


# The pass ------------------------------------------------------------------------------------


async def remember(
    db: Database,
    home: Path,
    runtime: AgentRuntime | None,
    *,
    model: str | None = None,
    check_model: str | None = None,
    max_autonomy: Autonomy = Autonomy.MEDIUM,
    now: datetime | None = None,
    should_stop: Callable[[], bool] = lambda: False,
) -> RememberReport:
    """One Remember pass over every project. Without a runtime the librarian and the checker
    are skipped and everything else still runs. ``should_stop`` lets a dream yield between
    cards. ``check_model`` checks entries against the project as it is (the librarian's model
    when not given); ``max_autonomy`` caps the rules the checker is told agents work under."""
    when = now or datetime.now(UTC)
    report = RememberReport()
    marks = _watermarks(db)
    open_runs = _open_runs(db)
    with db.session() as session:
        found = list(session.scalars(select(Project).where(Project.is_self.is_(False))))
        projects = [
            (p.id, p.slug, Path(p.agent_path), p.default_branch, Path(p.checkout_path))
            for p in found
        ]
        config_of = {p.slug: dict(p.config or {}) for p in found}
    for project_id, slug, agent, branch, checkout in projects:
        if should_stop():
            break
        kb = KnowledgeBase(home, slug)
        kb.ensure()
        result = ProjectReport(slug)
        report.projects.append(result)
        # (run id, card id, evidence, entry ids given, card still open)
        work: list[tuple[int, int, CardEvidence, list[str], bool]] = []
        still_open: list[int] = []
        with db.session() as session:
            for run_id in open_runs.get(slug, []):
                run = session.get(Run, run_id)
                if run is None:
                    continue
                if run.card.status not in SETTLED:
                    still_open.append(run_id)
                elif max(r.id for r in run.card.runs) == run_id:
                    # A later run of the same card is read on its own.
                    work.append((run_id, run.card_id, *gather(session, run, settled=True), False))
            runs = session.scalars(
                select(Run)
                .join(Card, Card.id == Run.card_id)
                .where(
                    Card.project_id == project_id,
                    Run.id > marks.get(slug, 0),
                    Run.status.in_(FINISHED),
                )
                .order_by(Run.id)
                .limit(MAX_RUNS_PER_PASS)
            )
            for run in runs:
                is_open = run.card.status not in SETTLED
                work.append((run.id, run.card_id, *gather(session, run), is_open))
        folded = _fold_runs_of_a_card(work)
        all_files: list[str] = []
        for index, (run_id, card_id, evidence, provided, is_open) in enumerate(work):
            if should_stop():
                # Settled runs not reached yet wait for the next pass.
                still_open += [item[0] for item in work[index:] if item[2].settled]
                break
            if evidence.settled:
                result.settled += 1
            else:
                result.ingested += 1
                all_files += evidence.changed_files
            landed = evidence.outcome.startswith("landed")
            refuted: set[str] = set()
            asks = evidence.has_signal or evidence.settled
            if runtime is not None and asks and run_id not in folded:
                related = related_entries(
                    kb.entries(),
                    evidence.changed_files,
                    SETTLED_RELATED if evidence.settled else RELATED,
                    card_id=card_id,
                    provided=provided,
                )
                answer = await propose(
                    runtime,
                    evidence,
                    related,
                    workdir=kb.root,
                    model=model,
                    limit=SETTLED_DELTAS if evidence.settled else MAX_DELTAS,
                )
                report.cost_usd += answer.cost_usd
                result.asked += 1
                if answer.error:
                    result.problems.append(f"card {card_id}: {answer.error}")
                # Landed code is decisive: what it made untrue stops being given to agents.
                applied = curator.apply(
                    kb,
                    answer.deltas,
                    source=Source(card_id, run_id, "librarian"),
                    decisive=evidence.settled and landed,
                )
                refuted = set(applied.reported)
                if applied.changed or applied.rejected:
                    when_read = "settled" if evidence.settled else "run"
                    result.changes.append(f"card {card_id} {when_read}: {applied.summary()}")
            if evidence.outcome == "landed first time" and provided:
                # Credit what the agents were given, except what this same card showed wrong.
                for entry_id in provided:
                    entry = kb.get(entry_id)
                    if entry is not None and entry.active and entry_id not in refuted:
                        entry.helped += 1
                        kb.save(entry)
                        result.helped += 1
            if not evidence.settled:
                if is_open:
                    still_open.append(run_id)
                _save_watermark(db, slug, run_id)
        _save_open_runs(db, slug, still_open)
        touches = _touches(kb, all_files)
        head = None
        if agent.exists():
            shown = Git(agent).run(
                "rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}", check=False
            )
            head = shown.stdout.strip() if shown.ok else None
        if head:
            try:
                verify(kb, agent, head, result, when)
                code = codemap.load_or_build(agent, head, kb.cache_dir())
                result.map_files = len(code.files)
                # A deep scan's inventory follows the code: rebuilt with tools, no model.
                inventory = load_inventory(kb.root)
                if inventory is not None and inventory.commit != head:
                    fresh = build_inventory(agent, head, installed=checkout, home=home)
                    save_inventory(kb.root, fresh)
                    result.inventory_refreshed = True
            except Exception as exc:
                log.exception("could not verify knowledge for %s", slug)
                result.problems.append(f"verify: {exc}")
        doubts: list[str] = []
        if head and runtime is not None and not should_stop():
            try:
                spec = _spec(agent, branch) or ProjectSpec.model_validate(
                    {"name": slug, "default_branch": branch, **config_of.get(slug, {})}
                )
                checked = await reconcile(
                    kb,
                    agent,
                    head,
                    runtime,
                    spec=spec,
                    autonomy=lowest_autonomy(max_autonomy, spec.autonomy),
                    model=check_model or model,
                    now=when,
                    should_stop=should_stop,
                )
            except Exception as exc:
                log.exception("could not check knowledge for %s", slug)
                result.problems.append(f"check: {exc}")
            else:
                report.cost_usd += checked.cost_usd
                result.checked = checked.checked
                result.check_wrong = checked.wrong
                result.check_refined = checked.refined
                result.check_unsure = checked.unsure
                result.problems += checked.problems
                doubts = checked.doubted_person
                if checked.wrong or checked.refined:
                    result.changes.append(
                        f"checked {checked.checked}: {checked.wrong} no longer true, "
                        f"{checked.refined} brought up to date"
                    )
        consolidate(kb, result, when)
        if runtime is not None and not should_stop():
            report.cost_usd += await judge_duplicates(
                kb, runtime, result, model=check_model or model, should_stop=should_stop
            )
        lint(kb, touches, result, doubts)
        kb.write_index()
        summary = ", ".join(result.changes) or "no new entries"
        read = f"{result.ingested} card run(s)"
        if result.settled:
            read += f", {result.settled} settled"
        result.commit = kb.commit(f"Remember: {read}; {summary}")
    return report
