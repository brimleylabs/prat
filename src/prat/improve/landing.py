"""Landing self cards: prove the merge, let the guard decide, merge, push.

For a self card in review:

1. Sync the self clone with its remote. A diverged default branch holds the
   card (it is not the card's fault) until a person reconciles the two.
2. Build the exact merge result (``--no-ff``) in a scratch worktree.
3. Prove that merge commit with the staged trial: the trusted invariant suite,
   the running system probes and the project's full gates. The proof is
   recorded per (merge base, card head), so it is not repeated every minute.
4. Ask the guard. ``land``: the engine merges the card for real, PRAT checks
   the real merge has the tree that was proven, and the result is pushed.
   ``refuse``: the card goes back to the builder with the evidence, and after
   ``max_reworks`` attempts it is abandoned and its finding marked failed.

In ``review`` mode the same proof runs and a proven card waits for a person's
approval; a merge is pushed only if its tree is one that was proven.

The push never comes from the agent clone, whose push block stays in force. It
goes through a separate bare repository in the PRAT home (agent sessions
cannot reach the PRAT home): fast-forward only, one explicit branch, no tags,
and only for the self project.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from prat import store
from prat.config import Settings
from prat.db import Card, Database, ErrorLog, Project, Run, as_utc
from prat.db.models import ImproveEvent, ImproveFinding
from prat.domain import (
    CardStatus,
    FindingSource,
    FindingStatus,
    Severity,
    StepName,
    StepStatus,
)
from prat.engine import ApproveError, Engine, EngineError
from prat.errors import record_error
from prat.git import Git
from prat.git.worktree import rmtree
from prat.improve.events import EventKind, record_event
from prat.improve.findings import FindingDraft, finding_fingerprint
from prat.improve.guard import Evidence, GuardResult, judge
from prat.improve.selfproject import get_self_project, load_self_info, sync_self_project
from prat.improve.settings import ImproveMode, ImproveSettings, load_improve_settings
from prat.improve.trial import TrialResult, TrialStatus, TrialStep, run_trial

log = logging.getLogger(__name__)

PUSH_RETRY_MINUTES = 15
OUTCOME_WINDOW_DAYS = 30
UNFIXABLE = (CardStatus.FAILED, CardStatus.BLOCKED)


class Outcome(StrEnum):
    LANDED = "landed"
    MERGED = "merged"  # merged locally; push off, unavailable or failed
    AWAITING_APPROVAL = "awaiting_approval"
    REWORKED = "reworked"
    ABANDONED = "abandoned"
    HELD = "held"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class LandingResult:
    card_id: int
    outcome: Outcome
    message: str


class Prover(Protocol):
    """Proves a merge commit. The real one is the staged trial."""

    def prove(self, base: str, merge_commit: str) -> TrialResult: ...


class OverlayProver:
    """Proves an overlay merge: every file of the exact merge commit is read the way PRAT
    reads the overlay at run time, and nothing may be switched off. The overlay cannot
    change PRAT's code, so there is nothing to install or boot."""

    def __init__(self, clone: Path) -> None:
        self.clone = clone

    def prove(self, base: str, merge_commit: str) -> TrialResult:
        from prat.overlay import load_overlay

        started = time.monotonic()
        git = Git(self.clone, timeout=60)
        with tempfile.TemporaryDirectory(prefix="prat-overlay-proof-") as tmp:
            root = Path(tmp)
            names = git.run("ls-tree", "-r", "-z", "--name-only", merge_commit).stdout
            for name in filter(None, names.split("\0")):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                blob = git.run("show", f"{merge_commit}:{name}")
                target.write_text(blob.stdout, encoding="utf-8")
            found = load_overlay(root)
        problems = [f"{issue.item}: {issue.reason}" for issue in found.disabled]
        ok = found.active and not problems
        message = (
            f"overlay valid: {len(found.loaded)} file(s) apply"
            if ok
            else "; ".join(problems) or "nothing in the overlay applies"
        )
        step = TrialStep("validate", ok, message, time.monotonic() - started)
        return TrialResult(merge_commit, TrialStatus.PASSED if ok else TrialStatus.FAILED, [step])


def default_prover(db: Database, settings: Settings, clone: Path) -> Prover:
    """The staged trial for PRAT's own source; validation for the overlay."""
    with db.session() as session:
        info = load_self_info(session)
    if info is not None and info.is_overlay:
        return OverlayProver(clone)
    return TrialProver(settings, db, clone)


class TrialProver:
    def __init__(self, settings: Settings, live: Database, clone: Path) -> None:
        self.settings = settings
        self.live = live
        self.clone = clone

    def prove(self, base: str, merge_commit: str) -> TrialResult:
        return run_trial(
            self.settings,
            self.live,
            clone=self.clone,
            base=base,
            candidate=merge_commit,
            probes=True,
            invariants=True,
            gates=True,
        )


@dataclass(frozen=True)
class PushResult:
    ok: bool
    pushed: bool
    message: str
    old: str | None = None
    new: str | None = None


# Helpers ------------------------------------------------------------------------------


def _events(session: Session, card_id: int, *kinds: EventKind) -> list[ImproveEvent]:
    query = (
        select(ImproveEvent)
        .where(ImproveEvent.card_id == card_id, ImproveEvent.kind.in_([str(k) for k in kinds]))
        .order_by(ImproveEvent.id)
    )
    return list(session.scalars(query))


def _finding_for(session: Session, card_id: int) -> ImproveFinding | None:
    return session.scalars(select(ImproveFinding).where(ImproveFinding.card_id == card_id)).first()


def _runs(session: Session, card_id: int) -> int:
    return len(list(session.scalars(select(Run.id).where(Run.card_id == card_id))))


def _evidence(session: Session, card: Card, head: str) -> dict[str, Any]:
    run = store.latest_run(session, card)
    steps = {step.name: step for step in run.steps} if run is not None else {}
    verify, critique = steps.get(StepName.VERIFY), steps.get(StepName.CRITIQUE)
    verify_ok = verify is not None and verify.status == StepStatus.SUCCEEDED
    vdetail = dict(verify.detail or {}) if verify is not None else {}
    cdetail = (
        dict(critique.detail or {})
        if critique is not None and critique.status == StepStatus.SUCCEEDED
        else {}
    )
    security = cdetail.get("security") if isinstance(cdetail.get("security"), dict) else {}
    gates = [g for g in vdetail.get("gates", []) if isinstance(g, dict)]
    return {
        "branch_head": head,
        "verified_commit": vdetail.get("head_commit") if verify_ok else None,
        "gates_passed": verify_ok and all(bool(g.get("passed")) for g in gates),
        "verify_findings": tuple(
            str(f.get("message", f)) for f in vdetail.get("findings", []) if f
        ),
        "criteria_verdict": cdetail.get("verdict"),
        "criteria_commit": cdetail.get("head_commit"),
        "security_verdict": (security or {}).get("verdict"),
        "security_commit": (security or {}).get("head_commit"),
    }


def build_merge(
    clone: Path, scratch: Path, default: str, branch: str, message: str
) -> tuple[str, str | None, str | None]:
    """Make the ``--no-ff`` merge of ``branch`` into ``default`` in a scratch
    worktree. Returns (base, merge commit or None, conflict description or None).
    The merge commit stays in the clone's object store; no branch moves."""
    git = Git(clone, timeout=300)
    base = git.rev_parse(f"refs/heads/{default}")
    if scratch.exists():
        git.run("worktree", "remove", "--force", str(scratch), check=False)
        rmtree(scratch)
    git.run("worktree", "prune", check=False)
    scratch.parent.mkdir(parents=True, exist_ok=True)
    git.run("worktree", "add", "--detach", "--force", str(scratch), base)
    try:
        work = Git(scratch, timeout=300)
        merged = work.run("merge", "--no-ff", "--no-edit", "-m", message, branch, check=False)
        if not merged.ok:
            files = work.run("diff", "--name-only", "--diff-filter=U", check=False).stdout
            conflict = (
                ", ".join(f for f in files.splitlines() if f.strip()) or merged.stderr.strip()
            )
            work.run("merge", "--abort", check=False)
            return base, None, conflict or "the merge failed"
        return base, work.head(), None
    finally:
        git.run("worktree", "remove", "--force", str(scratch), check=False)
        rmtree(scratch)
        git.run("worktree", "prune", check=False)


# The push ------------------------------------------------------------------------------


def publish_repo(settings: Settings) -> Path:
    return settings.home / "self" / "publish.git"


def publish(db: Database, settings: Settings, *, project_id: int) -> PushResult:
    """Push the self clone's default branch to the remote, fast-forward only."""
    with db.session() as session:
        project = session.get(Project, project_id)
        if project is None or not project.is_self:
            raise PermissionError("PRAT pushes only its own repository (the self project)")
        conf = load_improve_settings(session)
        info = load_self_info(session)
        clone, default = Path(project.agent_path), project.default_branch
    if not conf.push_enabled:
        return PushResult(True, False, "push is turned off; merged locally only")
    if info is None or not info.push_available:
        return PushResult(True, False, "no remote to push to; merged locally only")
    if info.is_overlay:
        return _apply_to_overlay(Path(info.origin), clone, default)
    repo = publish_repo(settings)
    if not (repo / "HEAD").exists():
        repo.mkdir(parents=True, exist_ok=True)
        Git(repo, timeout=60).run("init", "--bare", "--quiet")
    git = Git(repo, timeout=600)
    # No hooks run here, the person's global ones included: an empty folder of PRAT's own.
    (repo / "prat-hooks").mkdir(exist_ok=True)
    git.config_set("core.hooksPath", (repo / "prat-hooks").resolve().as_posix())
    git.run("fetch", "--no-tags", "--quiet", str(clone), f"+refs/heads/{default}:refs/PRAT/landed")
    new = git.rev_parse("refs/PRAT/landed")
    fetched = git.run(
        "fetch",
        "--no-tags",
        "--quiet",
        info.origin,
        f"+refs/heads/{default}:refs/PRAT/remote",
        check=False,
    )
    old = (
        git.rev_parse("refs/PRAT/remote")
        if fetched.ok and git.ref_exists("refs/PRAT/remote")
        else None
    )
    if not fetched.ok and "couldn't find remote ref" not in fetched.stderr.lower():
        return PushResult(False, False, f"could not reach the remote: {_tail(fetched.stderr)}")
    if old == new:
        return PushResult(True, False, "the remote is up to date", old, new)
    if old is not None and not git.run("merge-base", "--is-ancestor", old, new, check=False).ok:
        return PushResult(False, False, "the remote has moved on; nothing is forced", old, new)
    pushed = git.run(
        "push",
        "--porcelain",
        "--no-follow-tags",
        info.origin,
        f"refs/PRAT/landed:refs/heads/{default}",
        check=False,
    )
    if not pushed.ok:
        return PushResult(
            False, False, f"push failed: {_tail(pushed.stderr or pushed.stdout)}", old, new
        )
    return PushResult(True, True, f"pushed {(old or 'nothing')[:10]}..{new[:10]}", old, new)


def _apply_to_overlay(live_path: Path, clone: Path, default: str) -> PushResult:
    """Fast-forward the live overlay to the landed work. Never forced: uncommitted edits
    or the overlay having moved on hold the work back until a person sorts it out."""
    live = Git(live_path, timeout=60)
    if live.is_dirty():
        return PushResult(False, False, "the overlay has uncommitted changes; landed work waits")
    if live.current_branch() != default:
        return PushResult(False, False, f"the overlay is not on {default}; landed work waits")
    live.run("fetch", "--no-tags", "--quiet", str(clone), f"+refs/heads/{default}:refs/PRAT/landed")
    new, old = live.rev_parse("refs/PRAT/landed"), live.head()
    if old == new:
        return PushResult(True, False, "the overlay is up to date", old, new)
    if not live.run("merge-base", "--is-ancestor", old, new, check=False).ok:
        return PushResult(False, False, "the overlay has moved on; nothing is forced", old, new)
    merged = live.run("merge", "--ff-only", "--quiet", "refs/PRAT/landed", check=False)
    if not merged.ok:
        reason = _tail(merged.stderr or merged.stdout)
        return PushResult(False, False, f"could not update the overlay: {reason}", old, new)
    return PushResult(True, True, f"applied to the overlay {old[:10]}..{new[:10]}", old, new)


def _tree(repo: Path, commit: str) -> str:
    """The tree a commit records; two merges with the same tree land the same code."""
    return Git(repo, timeout=60).out("rev-parse", "--verify", f"{commit}^{{tree}}")


def _tail(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1][:300] if lines else "no output"


# Rework and abandon --------------------------------------------------------------------


def _send_back(
    db: Database, engine: Engine, conf: ImproveSettings, card_id: int, why: str, feedback: str
) -> LandingResult:
    with db.session() as session:
        runs = _runs(session, card_id)
    if runs <= conf.max_reworks:
        try:
            engine.rework(card_id, feedback)
        except EngineError as exc:
            return LandingResult(card_id, Outcome.HELD, f"could not send back: {exc}")
        with db.session() as session:
            record_event(
                session,
                EventKind.CARD_REWORKED,
                f"card #{card_id} sent back "
                f"({runs} of {conf.max_reworks + 1} attempts used): {why}",
                card_id=card_id,
                data={"attempts": runs, "reason": why},
            )
        return LandingResult(card_id, Outcome.REWORKED, why)
    return abandon(db, card_id, f"{why} (after {runs} attempts)")


def abandon(db: Database, card_id: int, why: str) -> LandingResult:
    """Cancel a self card that could not be proven. Its branch is kept; its finding
    is marked failed and waits out the cooldown."""
    with db.session() as session:
        card = store.get_card(session, card_id)
        if card.status not in (CardStatus.DONE, CardStatus.CANCELLED):
            if card.status == CardStatus.RUNNING:
                return LandingResult(card_id, Outcome.HELD, "the card is running")
            store.set_card_status(session, card, CardStatus.CANCELLED, f"abandoned: {why}"[:500])
        finding = _finding_for(session, card_id)
        if finding is not None and finding.status in (FindingStatus.CARDED, FindingStatus.NEW):
            finding.status = FindingStatus.FAILED
            finding.decision_note = f"abandoned: {why}"[:4000]
        record_event(
            session,
            EventKind.CARD_ABANDONED,
            f"card #{card_id} abandoned: {why}",
            card_id=card_id,
            data={
                "finding_id": finding.id if finding else None,
                "seen_count": finding.seen_count if finding else None,
                "reason": why,
            },
        )
    return LandingResult(card_id, Outcome.ABANDONED, why)


# Landing --------------------------------------------------------------------------------


def _proof_event(session: Session, card_id: int, base: str, head: str) -> ImproveEvent | None:
    for event in reversed(
        _events(session, card_id, EventKind.TRIAL_PASSED, EventKind.TRIAL_FAILED)
    ):
        data = event.data or {}
        if data.get("base") == base and data.get("head") == head:
            return event
    return None


async def land_self_card(
    db: Database,
    settings: Settings,
    engine: Engine,
    card_id: int,
    *,
    prover: Prover | None = None,
    sync: Callable[..., Any] = sync_self_project,
) -> LandingResult:
    """Prove a self card in review and land it, send it back, or hold it."""
    with db.session() as session:
        conf = load_improve_settings(session)
        card = store.get_card(session, card_id)
        project = card.project
        if not project.is_self:
            return LandingResult(card_id, Outcome.SKIPPED, "not a self card")
        if card.status != CardStatus.REVIEW or not card.branch:
            return LandingResult(card_id, Outcome.SKIPPED, f"the card is {card.status}")
        if not conf.enabled:
            return LandingResult(card_id, Outcome.SKIPPED, "self-improvement is off")
        clone, default, branch, title = (
            Path(project.agent_path),
            project.default_branch,
            card.branch,
            card.title,
        )
        project_id = project.id

    synced = await asyncio.to_thread(sync, db, settings)
    if not synced.ok:
        with db.session() as session:
            record_event(
                session,
                EventKind.LANDING_REFUSED,
                f"held: {synced.message}",
                card_id=card_id,
                data={"hold": True, "reasons": [synced.message]},
            )
        return LandingResult(card_id, Outcome.HELD, synced.message)

    head = await asyncio.to_thread(Git(clone).rev_parse, f"refs/heads/{branch}")
    with db.session() as session:
        card = store.get_card(session, card_id)
        facts = _evidence(session, card, head)
    pre = judge(Evidence(**facts, trial_status="passed"))
    if not pre.lands:
        return _refused(db, engine, conf, card_id, pre)

    scratch = settings.home / "self" / "merges" / f"card-{card_id}"
    base, merge_commit, conflict = await asyncio.to_thread(
        build_merge, clone, scratch, default, branch, f"Merge card {card_id}: {title}"
    )
    if merge_commit is None:
        result = judge(Evidence(**facts, merge_conflict=conflict, trial_status="passed"))
        return _refused(db, engine, conf, card_id, result)

    with db.session() as session:
        cached = _proof_event(session, card_id, base, head)
        if cached is not None and cached.kind == str(EventKind.TRIAL_FAILED):
            # Already proven bad for this exact base and head: the rework path ran.
            return LandingResult(card_id, Outcome.HELD, "this version was already refused")
    if cached is not None and cached.kind == str(EventKind.TRIAL_PASSED):
        trial_status, trial_summary = "passed", "proven earlier"
        tree = (cached.data or {}).get("tree")
    else:
        prover = prover or default_prover(db, settings, clone)
        trial = await asyncio.to_thread(prover.prove, base, merge_commit)
        trial_status, trial_summary = str(trial.status), trial.summary
        tree = await asyncio.to_thread(_tree, clone, merge_commit)
        if trial.status is not TrialStatus.POSTPONED:
            with db.session() as session:
                record_event(
                    session,
                    EventKind.TRIAL_PASSED if trial.passed else EventKind.TRIAL_FAILED,
                    trial.summary,
                    card_id=card_id,
                    data={
                        "base": base,
                        "head": head,
                        "merge": merge_commit,
                        "tree": tree if trial.passed else None,
                        "steps": [step.to_dict() for step in trial.steps],
                    },
                )
    result = judge(
        Evidence(
            **facts,
            merge_commit=merge_commit,
            trial_status=trial_status,
            trial_summary=trial_summary,
        )
    )
    if not result.lands:
        return _refused(db, engine, conf, card_id, result)

    if conf.mode is ImproveMode.REVIEW:
        with db.session() as session:
            waiting = [
                e
                for e in _events(session, card_id, EventKind.AWAITING_APPROVAL)
                if (e.data or {}).get("head") == head
            ]
            if not waiting:
                record_event(
                    session,
                    EventKind.AWAITING_APPROVAL,
                    f"card #{card_id} is proven and waits for a person's approval",
                    card_id=card_id,
                    data={"head": head, "base": base, "tree": tree},
                )
        return LandingResult(card_id, Outcome.AWAITING_APPROVAL, "proven; waiting for approval")

    try:
        merged = await engine.approve(card_id, automatic=True)
    except ApproveError as exc:
        return LandingResult(card_id, Outcome.HELD, f"approve refused: {exc}")
    real_tree = await asyncio.to_thread(_tree, clone, merged.commit)
    if real_tree != tree:
        await asyncio.to_thread(_undo_merge, clone, default, base)
        with db.session() as session:
            record_event(
                session,
                EventKind.MERGE_UNDONE,
                "the real merge differs from the proven one; the merge was undone",
                card_id=card_id,
                data={"merge": merged.commit, "base": base},
            )
        abandon(db, card_id, "the real merge differed from the proven merge")
        return LandingResult(card_id, Outcome.ABANDONED, "merge differed from the proof")
    return await _after_merge(db, settings, card_id, project_id, merged.commit, base)


def _undo_merge(clone: Path, default: str, base: str) -> None:
    git = Git(clone, timeout=120)
    if git.current_branch() == default:
        git.run("reset", "--hard", "--quiet", base)
    else:
        git.run("update-ref", f"refs/heads/{default}", base)


async def _after_merge(
    db: Database, settings: Settings, card_id: int, project_id: int, commit: str, base: str
) -> LandingResult:
    pushed = await asyncio.to_thread(publish, db, settings, project_id=project_id)
    with db.session() as session:
        finding = _finding_for(session, card_id)
        data: dict[str, Any] = {
            "commit": commit,
            "base": base,
            "finding_id": finding.id if finding else None,
            "pushed": pushed.pushed,
        }
        if finding is not None:
            evidence = dict(finding.evidence or {})
            if evidence.get("rule") == "error":
                data["error_fingerprint"] = evidence.get("error_fingerprint")
            if pushed.ok:
                finding.status = FindingStatus.LANDED
        record_event(
            session,
            EventKind.PUSH_OK if pushed.ok else EventKind.PUSH_FAILED,
            pushed.message,
            card_id=card_id,
            data={"old": pushed.old, "new": pushed.new, "pushed": pushed.pushed},
        )
        record_event(
            session,
            EventKind.CARD_LANDED,
            f"card #{card_id} merged as {commit[:10]}",
            card_id=card_id,
            data=data,
        )
    if not pushed.ok:
        return LandingResult(card_id, Outcome.MERGED, pushed.message)
    return LandingResult(
        card_id, Outcome.LANDED if pushed.pushed else Outcome.MERGED, pushed.message
    )


def _refused(
    db: Database, engine: Engine, conf: ImproveSettings, card_id: int, result: GuardResult
) -> LandingResult:
    with db.session() as session:
        record_event(
            session,
            EventKind.LANDING_REFUSED,
            ("held: " if result.hold else "refused: ") + "; ".join(result.reasons),
            card_id=card_id,
            data={"hold": result.hold, "reasons": list(result.reasons)},
        )
    if result.hold:
        return LandingResult(card_id, Outcome.HELD, "; ".join(result.reasons))
    return _send_back(db, engine, conf, card_id, "; ".join(result.reasons), result.feedback())


# Tending every self card ----------------------------------------------------------------


async def tend_self_cards(
    db: Database, settings: Settings, engine: Engine, *, prover: Prover | None = None
) -> list[LandingResult]:
    """One pass over the self cards: land, send back or abandon, and push pending merges."""
    results: list[LandingResult] = []
    with db.session() as session:
        conf = load_improve_settings(session)
        project = get_self_project(session)
        if not conf.enabled or project is None:
            return results
        project_id = project.id
        cards = [
            (card.id, card.status, _runs(session, card.id))
            for card in session.scalars(select(Card).where(Card.project_id == project.id))
        ]
    for card_id, status, runs in cards:
        if engine.is_scheduled(card_id):
            continue
        try:
            if status == CardStatus.REVIEW:
                results.append(await land_self_card(db, settings, engine, card_id, prover=prover))
            elif status in UNFIXABLE:
                with db.session() as session:
                    reason = store.get_card(session, card_id).status_reason or str(status)
                results.append(
                    _send_back(
                        db, engine, conf, card_id, reason, f"The last run ended {status}: {reason}"
                    )
                )
            elif status == CardStatus.REWORK and runs > conf.max_reworks:
                results.append(abandon(db, card_id, f"still not proven after {runs} attempts"))
        except Exception as exc:
            log.exception("could not tend self card %s", card_id)
            record_error(
                db, "improve", type(exc).__name__, str(exc), exc=exc, detail={"card": card_id}
            )
    pending = await push_pending(db, settings, project_id)
    if pending is not None:
        results.append(pending)
    return results


async def push_pending(db: Database, settings: Settings, project_id: int) -> LandingResult | None:
    """Push merges that have not reached the remote yet: after a failed push, and
    in review mode after a person approved a proven card."""
    with db.session() as session:
        project = session.get(Project, project_id)
        info = load_self_info(session)
        conf = load_improve_settings(session)
        if project is None or info is None or not info.push_available or not conf.push_enabled:
            return None
        clone, default = Path(project.agent_path), project.default_branch
        recent_failure = session.scalars(
            select(ImproveEvent)
            .where(ImproveEvent.kind == str(EventKind.PUSH_FAILED))
            .order_by(ImproveEvent.id.desc())
        ).first()
        proven_trees = {
            (event.data or {}).get("tree")
            for event in session.scalars(
                select(ImproveEvent).where(ImproveEvent.kind == str(EventKind.TRIAL_PASSED))
            )
        }
    git = Git(clone, timeout=120)
    remote_ref = f"refs/remotes/origin/{default}"
    if not git.ref_exists(remote_ref):
        return None
    head, remote = git.rev_parse(f"refs/heads/{default}"), git.rev_parse(remote_ref)
    if head == remote or not git.run("merge-base", "--is-ancestor", remote, head, check=False).ok:
        return None
    if recent_failure is not None:
        when = as_utc(recent_failure.created_at)
        if when and datetime.now(UTC) - when < timedelta(minutes=PUSH_RETRY_MINUTES):
            return None
    tree = _tree(clone, head)
    if tree not in proven_trees:
        with db.session() as session:
            record_event(
                session,
                EventKind.LANDING_REFUSED,
                f"not pushed: {default} at {head[:10]} was not proven by a trial",
                data={"hold": True, "head": head},
            )
        return None
    pushed = await asyncio.to_thread(publish, db, settings, project_id=project_id)
    with db.session() as session:
        record_event(
            session,
            EventKind.PUSH_OK if pushed.ok else EventKind.PUSH_FAILED,
            pushed.message,
            data={"old": pushed.old, "new": pushed.new, "pushed": pushed.pushed},
        )
        if pushed.ok and pushed.pushed:
            for finding in session.scalars(
                select(ImproveFinding).where(ImproveFinding.status == FindingStatus.CARDED)
            ):
                card = session.get(Card, finding.card_id) if finding.card_id else None
                if card is not None and card.status == CardStatus.DONE:
                    finding.status = FindingStatus.LANDED
    return LandingResult(0, Outcome.LANDED if pushed.pushed else Outcome.MERGED, pushed.message)


# Findings after landing ------------------------------------------------------------------


def revive_failed_findings(session: Session, conf: ImproveSettings, now: datetime) -> list[int]:
    """Failed findings may be tried again after the cooldown, or sooner when the
    problem has been seen clearly more often since."""
    revived: list[int] = []
    for finding in session.scalars(
        select(ImproveFinding).where(ImproveFinding.status == FindingStatus.FAILED)
    ):
        abandoned = session.scalars(
            select(ImproveEvent)
            .where(
                ImproveEvent.kind == str(EventKind.CARD_ABANDONED),
                ImproveEvent.card_id == finding.card_id,
            )
            .order_by(ImproveEvent.id.desc())
        ).first()
        when = as_utc(abandoned.created_at) if abandoned is not None else None
        seen_then = (abandoned.data or {}).get("seen_count") if abandoned is not None else None
        cooled = when is None or now - when >= timedelta(days=conf.retry_cooldown_days)
        grown = isinstance(seen_then, int) and finding.seen_count >= seen_then * 2 + 3
        if cooled or grown:
            finding.status = FindingStatus.NEW
            finding.card_id = None
            revived.append(finding.id)
            record_event(
                session,
                EventKind.FINDING_RECORDED,
                f"tried again: {finding.title}",
                data={"finding_id": finding.id, "revived": True},
            )
    session.flush()
    return revived


def check_outcomes(db: Database, now: datetime | None = None) -> list[FindingDraft]:
    """A landed fix whose error comes back files a high-severity finding that names
    the landed commit and proposes reverting it."""
    end = now or datetime.now(UTC)
    since = end - timedelta(days=OUTCOME_WINDOW_DAYS)
    drafts: list[FindingDraft] = []
    with db.session() as session:
        landed = session.scalars(
            select(ImproveEvent).where(
                ImproveEvent.kind == str(EventKind.CARD_LANDED), ImproveEvent.created_at >= since
            )
        )
        for event in landed:
            data = event.data or {}
            fingerprint, commit = data.get("error_fingerprint"), data.get("commit")
            if not fingerprint or not commit:
                continue
            after = event.created_at
            count = len(
                list(
                    session.scalars(
                        select(ErrorLog.id).where(
                            ErrorLog.fingerprint == fingerprint, ErrorLog.created_at > after
                        )
                    )
                )
            )
            if count == 0:
                continue
            drafts.append(
                FindingDraft(
                    source=FindingSource.DIAGNOSTIC,
                    fingerprint=finding_fingerprint(FindingSource.DIAGNOSTIC, "outcome", commit),
                    title=f"A landed fix did not hold: {commit[:10]}",
                    problem=(
                        f"Commit {commit} (card #{event.card_id}) was landed to fix an error, and "
                        f"the same error has happened {count} times since."
                    ),
                    proposal=(
                        f"Revert commit {commit} if it caused other problems, or find the real "
                        "cause of the error and fix it with a test that reproduces it."
                    ),
                    acceptance_criteria=[
                        "the error no longer recurs for the reproduced case",
                        f"the change made by {commit[:10]} is kept only if it is still needed",
                    ],
                    severity=Severity.HIGH,
                    evidence={
                        "rule": "outcome",
                        "landed_commit": commit,
                        "card_id": event.card_id,
                        "error_fingerprint": fingerprint,
                        "recurrences": count,
                    },
                )
            )
    return drafts
