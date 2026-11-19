"""Swarms: an epic's parts built at once and merged together on the epic's own branch.

Without a swarm, every part of an epic comes to review and lands on its own, and a part that
depends on another waits for the person to approve that one first: the person, not the agents,
sets the pace. With a swarm (``swarm_epics``, on by default), approving the split starts an
integration branch for the epic, made from the default branch:

1. Every part starts from the epic's branch, and parts that do not depend on each other run at
   the same time (the governor still holds a part until the parts it needs are in).
2. A part whose gates and reviewer pass is merged into the epic's branch by PRAT, not into the
   default branch, and the parts waiting on it start at once.
3. A part whose change conflicts with work already merged goes back to its builder, and its next
   run starts with the epic's branch merged into its own: the builder resolves the conflict in
   its worktree, where its gates and reviewer check the result (the integrator).
4. When every part is in, the project's gates run on the epic's branch, the whole feature. The
   epic then comes to review once, as one change, and approving it merges it like any card.

Nothing here reaches the default branch, the person's checkout or a remote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from prat import store
from prat.db import Card, Database
from prat.domain import CardKind, CardStatus
from prat.git.runner import Git, check_branch_name
from prat.git.worktree import MergeConflict, WorktreeManager, rmtree
from prat.projects import slugify

EPIC_PREFIX = "epic/"
# A part sent back this many times for conflicts with the epic's branch stays in review for the
# person instead of going round again.
MAX_CONFLICT_ROUNDS = 3
_MARKER = re.compile(r"^\+(<{7}|>{7})( |$)", re.MULTILINE)


class SwarmError(RuntimeError):
    pass


def epic_branch(epic_id: int, title: str) -> str:
    return check_branch_name(f"{EPIC_PREFIX}{epic_id}-{slugify(title, 40)}")


def is_swarm_epic(card: Card) -> bool:
    return card.kind is CardKind.EPIC and (card.branch or "").startswith(EPIC_PREFIX)


def start(db: Database, epic_id: int) -> list[int]:
    """The split was approved: make the epic's branch, point the parts at it and make them
    ready. The epic waits in ``building`` until every part is merged. Returns the ready parts."""
    with db.session() as session:
        epic = store.get_card(session, epic_id)
        project = epic.project
        branch = epic_branch(epic.id, epic.title)
        git = Git(Path(project.agent_path))
        if not git.branch_exists(branch):
            git.run("branch", branch, project.default_branch)
        epic.branch = branch
        ready: list[int] = []
        parts = list(session.scalars(select(Card).where(Card.parent_id == epic_id)))
        for part in parts:
            if part.status != CardStatus.DRAFT:
                continue
            part.base_branch = branch
            store.set_card_status(
                session, part, CardStatus.READY, f"part of epic #{epic_id}, built by a swarm"
            )
            ready.append(part.id)
        store.set_card_status(
            session,
            epic,
            CardStatus.BUILDING,
            f"a swarm is building its {len(ready)} parts on {branch}",
        )
    return ready


@dataclass
class Merged:
    commit: str
    epic_id: int
    remaining: int


def conflict_markers(worktrees: WorktreeManager, agent: Path, base: str, branch: str) -> bool:
    """Whether the part's change adds a line that is a conflict marker (an unresolved merge)."""
    merge_base = worktrees.merge_base(agent, base, branch)
    return bool(_MARKER.search(worktrees.diff_text(agent, merge_base, branch, context=0)))


def integrate(
    worktrees: WorktreeManager, agent: Path, epic_branch_name: str, part_branch: str, message: str
) -> str:
    """Merge a part into the epic's branch with ``--no-ff``, in a scratch worktree of the agent
    clone (the clone keeps its default branch checked out). Raises MergeConflict, with the merge
    abandoned and the epic's branch unchanged."""
    path = worktrees.worktrees_root / "_swarm" / slugify(epic_branch_name, 60)
    worktrees.remove_worktree(agent, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Git(agent).run("worktree", "add", "--quiet", str(path), epic_branch_name)
    try:
        git = Git(path)
        result = git.run(
            "merge", "--no-ff", "--no-edit", "--no-verify", "-m", message, part_branch, check=False
        )
        if not result.ok:
            conflicted = git.run("diff", "--name-only", "--diff-filter=U", check=False)
            files = [f for f in conflicted.stdout.splitlines() if f.strip()]
            git.run("merge", "--abort", check=False)
            raise MergeConflict(part_branch, files)
        return git.head()
    finally:
        worktrees.remove_worktree(agent, path)
        if path.exists():
            rmtree(path)


def conflict_note(base: str, files: list[str]) -> str:
    """What a part's builder is told when its run starts with the epic's work merged in."""
    listed = "\n".join(f"- {f}" for f in files)
    return (
        f"Other parts of this epic were merged into {base} while you worked, and PRAT has merged "
        f"{base} into your branch. These files have conflict markers to resolve:\n{listed}\n"
        "Keep both parts' intent: their work has been reviewed and is already in the epic. "
        "Remove every conflict marker; PRAT completes the merge when it commits your work."
    )


def conflict_rounds(db: Database, card_id: int) -> int:
    with db.session() as session:
        card = store.get_card(session, card_id)
        return sum(1 for c in card.comments if c.kind == "swarm_conflict")


def parts(db: Database, epic_id: int) -> list[Card]:
    with db.session() as session:
        found = list(
            session.scalars(select(Card).where(Card.parent_id == epic_id).order_by(Card.id))
        )
        session.expunge_all()
    return [p for p in found if p.status != CardStatus.CANCELLED]
