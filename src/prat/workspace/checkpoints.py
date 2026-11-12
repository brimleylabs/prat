"""Checkpoints: every landed card, with its code and data, and how to take it back.

A checkpoint is what approve recorded for one card: the git merge in the
project's agent clone and the merge into each project database. Two ways back:

``undo``    reverse just that card's code and data (``git revert`` of its
            merge, ``DOLT_REVERT`` of each database merge) and keep everything
            that landed since. Safe on live data. If either side conflicts,
            nothing changes.
``rewind``  put code and data back exactly as they were right after that card
            landed, dropping everything since. Needs explicit confirmation and
            tags the current state first, so a rewind can itself be undone.

Both work on PRAT's agent clone and the project databases; bring the code into
your own checkout afterwards as you would after any approve.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from prat import store
from prat.db import Card, Database, Event, Project, as_utc
from prat.dolt import versioning
from prat.git import Git
from prat.projects import ProjectSpec, parse_spec, show_project_file
from prat.workspace.dolt_branch import DoltBranchProvider, WorkspaceError, providers_for

log = logging.getLogger(__name__)


class CheckpointError(RuntimeError):
    pass


@dataclass(frozen=True)
class Checkpoint:
    card_id: int
    project_slug: str
    title: str
    landed_at: datetime | None
    git_commit: str
    branch: str
    databases: tuple[dict[str, Any], ...]
    undone: bool

    def describe(self) -> str:
        data = ", ".join(f"{d['database']}@{str(d['merge'])[:10]}" for d in self.databases)
        return f"git {self.git_commit[:10]}" + (f" + data {data}" if data else "")


@dataclass
class UndoResult:
    card_id: int
    git_revert: str
    databases: list[dict[str, str]] = field(default_factory=list)


@dataclass
class RewindResult:
    card_id: int
    backup_tag: str
    git_head: str
    databases: list[dict[str, str]] = field(default_factory=list)


def _events(db: Database, kind: str, card_id: int | None = None) -> list[Event]:
    with db.session() as session:
        query = select(Event).where(Event.kind == kind).order_by(Event.id.desc())
        if card_id is not None:
            query = query.where(Event.card_id == card_id)
        rows = list(session.scalars(query))
        for row in rows:
            session.expunge(row)
        return rows


def list_checkpoints(db: Database, *, project: str | None = None) -> list[Checkpoint]:
    """Newest first."""
    undone = {e.card_id for e in _events(db, "card.undone")}
    out: list[Checkpoint] = []
    with db.session() as session:
        for event in _events(db, "card.merged"):
            if event.card_id is None:
                continue
            card = session.get(Card, event.card_id)
            if card is None:
                continue
            slug = card.project.slug
            if project is not None and slug != project:
                continue
            data = dict(event.data or {})
            out.append(
                Checkpoint(
                    card_id=card.id,
                    project_slug=slug,
                    title=card.title,
                    landed_at=as_utc(event.created_at),
                    git_commit=str(data.get("commit", "")),
                    branch=str(data.get("branch", "")),
                    databases=tuple(d for d in data.get("databases") or [] if isinstance(d, dict)),
                    undone=card.id in undone,
                )
            )
    return out


def get_checkpoint(db: Database, card_id: int) -> Checkpoint:
    for checkpoint in list_checkpoints(db):
        if checkpoint.card_id == card_id:
            return checkpoint
    raise CheckpointError(f"card {card_id} has not landed, so there is no checkpoint for it")


def _project(db: Database, card_id: int) -> tuple[Path, str, ProjectSpec]:
    with db.session() as session:
        project: Project = store.get_card(session, card_id).project
        agent, default, config = Path(project.agent_path), project.default_branch, project.config
        name = project.name
    shown = show_project_file(Git(agent), default)
    if shown.ok:
        spec = parse_spec(shown.stdout)
    else:
        data = dict(config or {})
        data.setdefault("name", name)
        data.setdefault("default_branch", default)
        spec = ProjectSpec.model_validate(data)
    return agent, default, spec


def _providers(
    spec: ProjectSpec, checkpoint: Checkpoint, environ: Mapping[str, str] | None = None
) -> list[tuple[DoltBranchProvider, dict[str, Any]]]:
    by_name = {
        provider.spec.name: provider
        for provider in providers_for(spec.workspace.database, environ=environ)
    }
    pairs = []
    for merged in checkpoint.databases:
        provider = by_name.get(str(merged.get("name")))
        if provider is None:
            raise CheckpointError(
                f"database {merged.get('name')!r} from card {checkpoint.card_id} is no longer "
                "declared in the project file"
            )
        pairs.append((provider, merged))
    return pairs


def _clean_default(git: Git, default: str) -> None:
    if git.current_branch() != default:
        git.run("checkout", "--quiet", default)
    if git.is_dirty():
        raise CheckpointError("the agent clone has uncommitted changes; nothing was changed")


def undo_card(
    db: Database,
    card_id: int,
    *,
    actor: str = "person",
    environ: Mapping[str, str] | None = None,
) -> UndoResult:
    """Reverse one landed card's code and data, keeping everything since. ``environ`` is where
    the databases' URL variables are looked up (default: PRAT's environment)."""
    checkpoint = get_checkpoint(db, card_id)
    if checkpoint.undone:
        raise CheckpointError(f"card {card_id} has already been undone")
    agent, default, spec = _project(db, card_id)
    git = Git(agent)
    _clean_default(git, default)
    try:
        pairs = _providers(spec, checkpoint, environ)
    except WorkspaceError as exc:
        raise CheckpointError(str(exc)) from exc
    reverted: list[tuple[DoltBranchProvider, str, str]] = []
    try:
        for provider, merged in pairs:
            before = provider.default_head()
            after = provider.revert(str(merged["merge"]))
            reverted.append((provider, before, after))
    except Exception as exc:
        _restore(reverted)
        raise CheckpointError(f"the data of card {card_id} could not be reverted: {exc}") from exc
    result = git.run("revert", "-m", "1", "--no-edit", checkpoint.git_commit, check=False)
    if not result.ok:
        git.run("revert", "--abort", check=False)
        _restore(reverted)
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise CheckpointError(
            f"the code of card {card_id} could not be reverted, so nothing was changed: "
            f"{detail[-1] if detail else 'git revert failed'}"
        )
    outcome = UndoResult(
        card_id,
        git.head(),
        [
            {"name": p.spec.name, "database": p.database, "revert": after}
            for p, _before, after in reverted
        ],
    )
    with db.session() as session:
        card = store.get_card(session, card_id)
        store.record_event(
            session,
            "card.undone",
            project_id=card.project_id,
            card_id=card_id,
            data={"git_revert": outcome.git_revert, "databases": outcome.databases, "actor": actor},
        )
    data_note = "".join(f", data {d['database']}@{d['revert']}" for d in outcome.databases)
    versioning.commit(
        db, f"card {card_id} undone: git revert {outcome.git_revert}{data_note}", actor=actor
    )
    return outcome


def rewind_to(
    db: Database,
    card_id: int,
    *,
    confirm: bool = False,
    actor: str = "person",
    environ: Mapping[str, str] | None = None,
) -> RewindResult:
    """Put code and data back exactly as they were right after ``card_id`` landed."""
    checkpoint = get_checkpoint(db, card_id)
    if not confirm:
        raise CheckpointError(
            f"rewinding to card {card_id} drops everything that landed after it, in the code "
            "and in the data; confirm to go ahead (the current state is tagged first)"
        )
    agent, default, spec = _project(db, card_id)
    git = Git(agent)
    _clean_default(git, default)
    try:
        pairs = _providers(spec, checkpoint, environ)
    except WorkspaceError as exc:
        raise CheckpointError(str(exc)) from exc
    tag = f"prat-before-rewind-{datetime.now(UTC):%Y%m%d%H%M%S}"
    git.run("tag", tag, git.head())
    databases: list[dict[str, str]] = []
    for provider, merged in pairs:
        current = provider.default_head()
        provider.tag(tag, current, f"state before rewinding to card {card_id}")
        provider.reset_to(str(merged["merge"]))
        databases.append(
            {
                "name": provider.spec.name,
                "database": provider.database,
                "head": str(merged["merge"]),
            }
        )
    git.run("reset", "--hard", "--quiet", checkpoint.git_commit)
    outcome = RewindResult(card_id, tag, git.head(), databases)
    with db.session() as session:
        card = store.get_card(session, card_id)
        store.record_event(
            session,
            "project.rewound",
            project_id=card.project_id,
            card_id=card_id,
            data={
                "backup_tag": tag,
                "git_head": outcome.git_head,
                "databases": databases,
                "actor": actor,
            },
        )
    versioning.commit(
        db,
        f"project rewound to card {card_id}: git {outcome.git_head}, backup tag {tag}",
        actor=actor,
    )
    return outcome


def _restore(reverted: list[tuple[DoltBranchProvider, str, str]]) -> None:
    """Put databases back after a failed undo. The original error is the one reported;
    a failure here is logged loudly because it needs a person."""
    for provider, before, _after in reversed(reverted):
        try:
            provider.reset_to(before)
        except Exception:
            log.exception(
                "could not put %s back to %s after a failed undo", provider.database, before
            )
