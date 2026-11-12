"""Landing self cards: prove, guard, merge, push; rework, abandon, hold."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from conftest import WRITE_GREET, add_card, git, make_engine, make_repo, project_toml
from prat import store
from prat.config import Settings
from prat.db import Database, ErrorLog, Project
from prat.db.models import ImproveEvent, ImproveFinding
from prat.domain import CardStatus, FindingSource, FindingStatus
from prat.engine import Engine
from prat.errors import record_error
from prat.improve import landing as landing_module
from prat.improve.events import EventKind, record_event
from prat.improve.findings import FindingDraft, finding_fingerprint, record_findings
from prat.improve.landing import (
    Outcome,
    check_outcomes,
    land_self_card,
    publish,
    revive_failed_findings,
    tend_self_cards,
)
from prat.improve.selfproject import self_repo_path, setup_self_project
from prat.improve.settings import load_improve_settings, update_improve_settings
from prat.improve.trial import TrialResult, TrialStatus, TrialStep

pytestmark = pytest.mark.integration

OPEN_TOML = (
    project_toml()
    .replace('"acme-api"', '"prat"')
    .replace('protected_paths = ["migrations/**"]', "protected_paths = []")
)


class FakeProver:
    def __init__(self, status: TrialStatus = TrialStatus.PASSED, summary: str = "") -> None:
        self.status = status
        self.summary = summary
        self.calls: list[tuple[str, str]] = []

    def prove(self, base: str, merge_commit: str) -> TrialResult:
        self.calls.append((base, merge_commit))
        steps = [TrialStep("probe", self.status is TrialStatus.PASSED, self.summary or "scripted")]
        return TrialResult(merge_commit, self.status, steps)


@pytest.fixture
def remote(settings: Settings, db: Database, tmp_path: Path) -> Path:
    """PRAT's 'repository': a source checkout, a bare remote and the self project."""
    source = make_repo(
        tmp_path / "src",
        "prat",
        project_toml=OPEN_TOML,
        files={"src/prat/__init__.py": '"""PRAT."""\n'},
    )
    bare = tmp_path / "remote" / "prat.git"
    bare.parent.mkdir(parents=True)
    git(bare.parent, "clone", "-q", "--bare", str(source), str(bare))
    git(source, "remote", "add", "origin", str(bare))
    setup_self_project(db, settings, running_from=source / "src/prat/__init__.py")
    with db.session() as session:
        update_improve_settings(session, enabled=True)
    return bare


def self_project(db: Database) -> Project:
    with db.session() as session:
        project = session.scalars(select(Project).where(Project.is_self.is_(True))).one()
        session.expunge(project)
        return project


def configure(db: Database, **changes: Any) -> None:
    with db.session() as session:
        update_improve_settings(session, **changes)


def status(db: Database, card_id: int) -> CardStatus:
    with db.session() as session:
        return store.get_card(session, card_id).status


def kinds(db: Database, card_id: int | None = None) -> list[str]:
    with db.session() as session:
        query = select(ImproveEvent.kind).order_by(ImproveEvent.id)
        if card_id is not None:
            query = query.where(ImproveEvent.card_id == card_id)
        return list(session.scalars(query))


def with_finding(db: Database, card_id: int, rule: str = "error") -> int:
    draft = FindingDraft(
        source=FindingSource.DIAGNOSTIC,
        fingerprint=finding_fingerprint(FindingSource.DIAGNOSTIC, "test", str(card_id)),
        title=f"finding for card {card_id}",
        problem="p",
        proposal="q",
        acceptance_criteria=["c"],
        evidence={"rule": rule, "error_fingerprint": "f" * 64},
    )
    with db.session() as session:
        (recorded,) = record_findings(session, [draft])
        row = session.get(ImproveFinding, recorded.finding_id)
        assert row is not None
        row.status = FindingStatus.CARDED
        row.card_id = card_id
        return row.id


def finding_status(db: Database, finding_id: int) -> FindingStatus:
    with db.session() as session:
        row = session.get(ImproveFinding, finding_id)
        assert row is not None
        return row.status


async def reviewed_card(
    settings: Settings, db: Database, script: list[dict[str, Any]] | None = None
) -> tuple[Engine, int]:
    engine, _ = make_engine(settings, db, script or WRITE_GREET)
    card_id = add_card(db, self_project(db))
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW, result.reason
    return engine, card_id


# Landing -------------------------------------------------------------------------------


async def test_a_proven_card_lands_and_is_pushed(
    settings: Settings, db: Database, remote: Path, tmp_path: Path
) -> None:
    engine, card_id = await reviewed_card(settings, db)
    finding = with_finding(db, card_id)
    prover = FakeProver()
    result = await land_self_card(db, settings, engine, card_id, prover=prover)
    assert result.outcome is Outcome.LANDED, result.message
    assert status(db, card_id) is CardStatus.DONE

    clone = self_repo_path(settings)
    head = git(clone, "rev-parse", "main")
    assert git(remote, "rev-parse", "main") == head  # the bare remote has the merge
    ((base, proven),) = prover.calls
    assert git(clone, "rev-parse", f"{proven}^{{tree}}") == git(clone, "rev-parse", "main^{tree}")
    assert git(clone, "rev-parse", "main^1") == base
    assert finding_status(db, finding) is FindingStatus.LANDED
    events = kinds(db, card_id)
    for kind in ("trial_passed", "push_ok", "card_landed"):
        assert kind in events
    # The agent clone is still hardened against pushes.
    assert git(clone, "config", "remote.origin.pushurl").startswith("prat-push-disabled")
    # The source checkout PRAT 'runs from' is untouched.
    assert git(tmp_path / "src" / "prat", "status", "--porcelain") == ""


async def test_the_proof_is_not_repeated_for_the_same_version(
    settings: Settings, db: Database, remote: Path
) -> None:
    configure(db, mode="review")
    engine, card_id = await reviewed_card(settings, db)
    prover = FakeProver()
    first = await land_self_card(db, settings, engine, card_id, prover=prover)
    second = await land_self_card(db, settings, engine, card_id, prover=prover)
    assert first.outcome is second.outcome is Outcome.AWAITING_APPROVAL
    assert len(prover.calls) == 1
    assert kinds(db, card_id).count("awaiting_approval") == 1


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("src/prat/policy/engine.py", "ALLOW = False\n"),
        ("pyproject.toml", '[project]\nname = "prat"\ndependencies = ["httpx"]\n'),
        ("migrations/0002_add.sql", "ALTER TABLE x ADD COLUMN y TEXT;\n"),
        ("src/prat/improve/guard.py", "# a better guard\n"),
    ],
)
async def test_any_kind_of_change_lands_with_no_person_when_proven(
    settings: Settings, db: Database, remote: Path, path: str, content: str
) -> None:
    script = [{"op": "write", "path": path, "content": content}, {"op": "finish"}]
    engine, card_id = await reviewed_card(settings, db, script)
    result = await land_self_card(db, settings, engine, card_id, prover=FakeProver())
    assert result.outcome is Outcome.LANDED, result.message
    assert git(remote, "show", f"main:{path}").strip() == content.strip()


# Refusal: rework, then abandon -----------------------------------------------------------


async def test_a_refused_card_goes_back_with_the_evidence(
    settings: Settings, db: Database, remote: Path
) -> None:
    engine, card_id = await reviewed_card(settings, db)
    prover = FakeProver(TrialStatus.FAILED, "the probe saw a page without a session")
    result = await land_self_card(db, settings, engine, card_id, prover=prover)
    assert result.outcome is Outcome.REWORKED
    assert status(db, card_id) is CardStatus.REWORK
    with db.session() as session:
        card = store.get_card(session, card_id)
        feedback = store.latest_human_comment(session, card)
        assert feedback is not None and "staged trial failed" in feedback
    assert "landing_refused" in kinds(db, card_id) and "card_reworked" in kinds(db, card_id)
    assert git(remote, "rev-parse", "main") == git(
        self_repo_path(settings), "rev-parse", "origin/main"
    )


async def test_after_the_limit_a_card_is_abandoned(
    settings: Settings, db: Database, remote: Path
) -> None:
    configure(db, max_reworks=0)
    engine, card_id = await reviewed_card(settings, db)
    finding = with_finding(db, card_id)
    result = await land_self_card(
        db, settings, engine, card_id, prover=FakeProver(TrialStatus.FAILED, "boom")
    )
    assert result.outcome is Outcome.ABANDONED
    assert status(db, card_id) is CardStatus.CANCELLED
    assert finding_status(db, finding) is FindingStatus.FAILED
    with db.session() as session:
        branch = store.get_card(session, card_id).branch
    assert branch and git(self_repo_path(settings), "rev-parse", "--verify", branch)
    assert "card_abandoned" in kinds(db, card_id)


async def test_failed_and_stuck_self_cards_are_tended(
    settings: Settings, db: Database, remote: Path
) -> None:
    from conftest import project_toml as toml

    configure(db, max_reworks=1)
    # A gate failure on a self card: sent back automatically with the reason.
    clone = self_repo_path(settings)
    (clone / ".prat" / "project.toml").write_text(
        toml(test_code="import sys; sys.exit(1)").replace('"acme-api"', '"prat"'),
        encoding="utf-8",
    )
    git(clone, "commit", "-q", "-am", "failing gate for this test")
    engine, _ = make_engine(settings, db, WRITE_GREET)
    card_id = add_card(db, self_project(db))
    assert (await engine.run_card(card_id)).card_status is CardStatus.FAILED
    (first,) = await tend_self_cards(db, settings, engine, prover=FakeProver())
    assert first.outcome is Outcome.REWORKED
    assert status(db, card_id) is CardStatus.REWORK
    assert (await engine.run_card(card_id)).card_status is CardStatus.FAILED
    (second,) = await tend_self_cards(db, settings, engine, prover=FakeProver())
    assert second.outcome is Outcome.ABANDONED
    assert status(db, card_id) is CardStatus.CANCELLED


# Holding: the card cannot fix these ------------------------------------------------------


async def test_a_diverged_remote_holds_without_using_an_attempt(
    settings: Settings, db: Database, remote: Path, tmp_path: Path
) -> None:
    engine, card_id = await reviewed_card(settings, db)
    clone = self_repo_path(settings)
    (clone / "local.txt").write_text("x", encoding="utf-8")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "local only")
    pusher = tmp_path / "pusher"
    git(tmp_path, "clone", "-q", str(remote), str(pusher))
    git(pusher, "config", "user.name", "T")
    git(pusher, "config", "user.email", "t@example.invalid")
    (pusher / "remote.txt").write_text("y", encoding="utf-8")
    git(pusher, "add", "-A")
    git(pusher, "commit", "-q", "-m", "remote only")
    git(pusher, "push", "-q", "origin", "main")
    result = await land_self_card(db, settings, engine, card_id, prover=FakeProver())
    assert result.outcome is Outcome.HELD and "diverged" in result.message
    assert status(db, card_id) is CardStatus.REVIEW


async def test_a_postponed_trial_holds(settings: Settings, db: Database, remote: Path) -> None:
    engine, card_id = await reviewed_card(settings, db)
    prover = FakeProver(TrialStatus.POSTPONED, "the package index is unreachable")
    result = await land_self_card(db, settings, engine, card_id, prover=prover)
    assert result.outcome is Outcome.HELD
    assert status(db, card_id) is CardStatus.REVIEW
    assert "card_reworked" not in kinds(db, card_id)


async def test_nothing_lands_while_off(settings: Settings, db: Database, remote: Path) -> None:
    engine, card_id = await reviewed_card(settings, db)
    configure(db, enabled=False)
    prover = FakeProver()
    result = await land_self_card(db, settings, engine, card_id, prover=prover)
    assert result.outcome is Outcome.SKIPPED
    assert prover.calls == []
    assert await tend_self_cards(db, settings, engine, prover=prover) == []


# The real merge must be the proven one ---------------------------------------------------


async def test_a_real_merge_that_differs_from_the_proof_is_undone(
    settings: Settings, db: Database, remote: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, card_id = await reviewed_card(settings, db)
    clone = self_repo_path(settings)
    real_build = landing_module.build_merge

    def build_something_else(*args: Any) -> tuple[str, str | None, str | None]:
        base, _merge, conflict = real_build(*args)
        empty = git(clone, "hash-object", "-t", "tree", "--stdin", "-w")
        other = git(clone, "commit-tree", empty, "-p", base, "-m", "not the card")
        return base, other, conflict

    monkeypatch.setattr(landing_module, "build_merge", build_something_else)
    before = git(clone, "rev-parse", "main")
    result = await land_self_card(db, settings, engine, card_id, prover=FakeProver())
    assert result.outcome is Outcome.ABANDONED
    assert git(clone, "rev-parse", "main") == before
    assert git(remote, "rev-parse", "main") == before
    assert "merge_undone" in kinds(db, card_id)


# Review mode -----------------------------------------------------------------------------


async def test_review_mode_waits_for_a_person_then_pushes_the_proven_merge(
    settings: Settings, db: Database, remote: Path
) -> None:
    configure(db, mode="review")
    engine, card_id = await reviewed_card(settings, db)
    result = await land_self_card(db, settings, engine, card_id, prover=FakeProver())
    assert result.outcome is Outcome.AWAITING_APPROVAL
    assert status(db, card_id) is CardStatus.REVIEW
    before = git(remote, "rev-parse", "main")
    await engine.approve(card_id)  # the person approves
    results = await tend_self_cards(db, settings, engine, prover=FakeProver())
    assert any(r.outcome is Outcome.LANDED for r in results), results
    assert git(remote, "rev-parse", "main") != before
    assert git(remote, "rev-parse", "main") == git(self_repo_path(settings), "rev-parse", "main")


async def test_an_unproven_merge_is_never_pushed(
    settings: Settings, db: Database, remote: Path
) -> None:
    configure(db, mode="review")
    engine, card_id = await reviewed_card(settings, db)
    before = git(remote, "rev-parse", "main")
    await engine.approve(card_id)  # approved before any proof ran
    with db.session() as session:
        project = session.scalars(select(Project).where(Project.is_self.is_(True))).one()
        project_id = project.id
    pending = await landing_module.push_pending(db, settings, project_id)
    assert pending is None
    assert git(remote, "rev-parse", "main") == before
    assert "landing_refused" in kinds(db)


# The push --------------------------------------------------------------------------------


async def test_the_push_is_fast_forward_only(
    settings: Settings, db: Database, remote: Path, tmp_path: Path
) -> None:
    project = self_project(db)
    clone = self_repo_path(settings)
    (clone / "landed.txt").write_text("x", encoding="utf-8")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "landed locally")
    pusher = tmp_path / "pusher"
    git(tmp_path, "clone", "-q", str(remote), str(pusher))
    git(pusher, "config", "user.name", "T")
    git(pusher, "config", "user.email", "t@example.invalid")
    (pusher / "other.txt").write_text("y", encoding="utf-8")
    git(pusher, "add", "-A")
    git(pusher, "commit", "-q", "-m", "someone else")
    git(pusher, "push", "-q", "origin", "main")
    theirs = git(remote, "rev-parse", "main")
    result = publish(db, settings, project_id=project.id)
    assert not result.ok and "moved on" in result.message
    assert git(remote, "rev-parse", "main") == theirs
    assert git(remote, "tag") == ""


def test_the_push_refuses_any_other_project(
    settings: Settings, db: Database, project: Project
) -> None:
    with pytest.raises(PermissionError, match="only its own repository"):
        publish(db, settings, project_id=project.id)


async def test_with_push_off_work_is_merged_locally(
    settings: Settings, db: Database, remote: Path
) -> None:
    configure(db, push_enabled=False)
    engine, card_id = await reviewed_card(settings, db)
    before = git(remote, "rev-parse", "main")
    result = await land_self_card(db, settings, engine, card_id, prover=FakeProver())
    assert result.outcome is Outcome.MERGED
    assert status(db, card_id) is CardStatus.DONE
    assert git(remote, "rev-parse", "main") == before


# After landing ---------------------------------------------------------------------------


def test_a_recurring_error_after_landing_files_a_revert_finding(db: Database) -> None:
    fingerprint = "e" * 64
    with db.session() as session:
        record_event(
            session,
            EventKind.CARD_LANDED,
            "landed",
            data={"commit": "c" * 40, "error_fingerprint": fingerprint},
        )
    assert check_outcomes(db) == []
    with db.session() as session:
        session.add(
            ErrorLog(
                source="engine", kind="OSError", message="again", detail={}, fingerprint=fingerprint
            )
        )
    (draft,) = check_outcomes(db)
    assert draft.severity.value == "high"
    assert ("c" * 10) in draft.title and "Revert" in draft.proposal
    record_error(db, "engine", "Other", "unrelated")
    assert len(check_outcomes(db)) == 1


def test_failed_findings_come_back_after_the_cooldown(db: Database) -> None:
    draft = FindingDraft(
        source=FindingSource.RESEARCH,
        fingerprint=finding_fingerprint(FindingSource.RESEARCH, "x"),
        title="x",
        problem="p",
        proposal="q",
        acceptance_criteria=["c"],
    )
    with db.session() as session:
        (recorded,) = record_findings(session, [draft])
        row = session.get(ImproveFinding, recorded.finding_id)
        assert row is not None
        row.status = FindingStatus.FAILED
        record_event(session, EventKind.CARD_ABANDONED, "gone", data={"seen_count": 1})
    with db.session() as session:
        conf = load_improve_settings(session)
        assert revive_failed_findings(session, conf, datetime.now(UTC)) == []
        later = datetime.now(UTC) + timedelta(days=conf.retry_cooldown_days + 1)
        assert revive_failed_findings(session, conf, later) == [recorded.finding_id]
    with db.session() as session:
        row = session.get(ImproveFinding, recorded.finding_id)
        assert row is not None and row.status is FindingStatus.NEW


# The whole loop -------------------------------------------------------------------------


async def test_a_full_cycle_ends_with_the_commit_in_the_remote(
    settings: Settings, db: Database, remote: Path
) -> None:
    import asyncio

    from prat.improve.scheduler import ImproveScheduler
    from prat.runtime import AgentTask, FakeRuntime, FakeScript

    configure(db, max_new_cards_per_cycle=1)
    for _ in range(3):
        record_error(db, "engine", "OSError", "disk fell over")

    def script(task: AgentTask) -> FakeScript:
        if task.metadata.get("role") == "researcher":
            return FakeScript.parse([{"op": "finish", "message": '{"proposals": []}'}])
        return FakeScript.parse(WRITE_GREET)

    engine, _ = make_engine(settings, db, FakeRuntime(script))
    scheduler = ImproveScheduler(db, settings, engine, tick_seconds=3600, prover=FakeProver())
    before = git(remote, "rev-parse", "main")
    await scheduler.tick()  # starts the cycle (and a landing pass with nothing to land)
    assert scheduler._cycle is not None
    cycle = await scheduler._cycle
    (card_id,) = cycle.cards
    for _ in range(400):
        if status(db, card_id) is CardStatus.REVIEW and not engine.is_scheduled(card_id):
            break
        await asyncio.sleep(0.05)
    assert status(db, card_id) is CardStatus.REVIEW
    if scheduler._landing is not None:
        await scheduler._landing
    await scheduler.tick()  # lands it
    assert scheduler._landing is not None
    results = await scheduler._landing
    assert any(r.outcome is Outcome.LANDED for r in results), results
    assert status(db, card_id) is CardStatus.DONE
    after = git(remote, "rev-parse", "main")
    assert after != before
    assert git(remote, "show", f"{after}:hello.py").startswith("def greet")
    await scheduler.stop()
