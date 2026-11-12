"""Small data-access helpers shared by the engine and the CLI.

Status changes go through the model validators (and so through the domain
state machines); these helpers add the audit event alongside each change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from prat import paths
from prat.db import as_utc
from prat.db.models import Card, Comment, Event, Project, Run
from prat.domain import (
    Autonomy,
    CardKind,
    CardStatus,
    CommentAuthor,
    Complexity,
    RunStatus,
)
from prat.projects import ProjectSpec, check_project_paths, validate_slug


class NotFound(LookupError):
    pass


def record_event(
    session: Session,
    kind: str,
    *,
    project_id: int | None = None,
    card_id: int | None = None,
    run_id: int | None = None,
    data: dict[str, Any] | None = None,
) -> Event:
    event = Event(kind=kind, project_id=project_id, card_id=card_id, run_id=run_id, data=data or {})
    session.add(event)
    return event


# Projects --------------------------------------------------------------------


def add_project(
    session: Session,
    *,
    slug: str,
    checkout_path: Path,
    agent_path: Path,
    spec: ProjectSpec,
) -> Project:
    validate_slug(slug)
    checkout = paths.resolve(checkout_path)
    agent = paths.resolve(agent_path)
    check_project_paths(checkout, agent)
    for other in session.scalars(select(Project)):
        if other.slug == slug:
            raise ValueError(f"project {slug!r} already exists")
        for mine in (checkout, agent):
            for theirs in (other.checkout_path, other.agent_path):
                if paths.is_within(mine, theirs) or paths.is_within(theirs, mine):
                    raise ValueError(f"{mine} overlaps paths of project {other.slug!r}")
    project = Project(
        slug=slug,
        name=spec.name,
        checkout_path=str(checkout),
        agent_path=str(agent),
        default_branch=spec.default_branch,
        config=spec.model_dump(mode="json"),
    )
    session.add(project)
    session.flush()
    record_event(session, "project.added", project_id=project.id, data={"slug": slug})
    return project


def get_project(session: Session, ref: str | int) -> Project:
    project: Project | None
    if isinstance(ref, int) or str(ref).isdigit():
        project = session.get(Project, int(ref))
    else:
        project = session.scalars(select(Project).where(Project.slug == ref)).first()
    if project is None:
        raise NotFound(f"no project {ref!r}")
    return project


def list_projects(session: Session) -> list[Project]:
    return list(session.scalars(select(Project).order_by(Project.slug)))


def remove_project(session: Session, ref: str | int) -> tuple[str, Path, Path]:
    """Forget a project: its cards, runs and records go with it (foreign keys cascade).
    Refused while one of its cards is running. Returns the slug, checkout and agent clone,
    which the caller decides about; nothing on disk is touched here."""
    project = get_project(session, ref)
    busy = session.scalars(
        select(Card.id).where(Card.project_id == project.id, Card.status == CardStatus.RUNNING)
    ).first()
    if busy is not None:
        raise ValueError(f"card {busy} of {project.slug} is running; stop it first")
    found = (project.slug, Path(project.checkout_path), Path(project.agent_path))
    session.execute(delete(Project).where(Project.id == project.id))
    record_event(session, "project.removed", data={"slug": project.slug})
    return found


# Cards -----------------------------------------------------------------------


def add_card(
    session: Session,
    project: Project,
    *,
    title: str,
    goal: str = "",
    acceptance_criteria: str = "",
    complexity: Complexity = Complexity.MEDIUM,
    ready: bool = False,
    autonomy: Autonomy | None = None,
    budget_usd: float | None = None,
    kind: CardKind = CardKind.CHANGE,
) -> Card:
    if not title.strip():
        raise ValueError("card title must not be empty")
    card = Card(
        project=project,
        title=title.strip(),
        goal=goal,
        acceptance_criteria=acceptance_criteria,
        complexity=complexity,
        kind=kind,
        status=CardStatus.READY if ready else CardStatus.DRAFT,
        autonomy=autonomy,
        budget_usd=budget_usd,
    )
    session.add(card)
    session.flush()
    record_event(
        session,
        "card.added",
        project_id=project.id,
        card_id=card.id,
        data={"status": str(card.status)},
    )
    return card


def get_card(session: Session, card_id: int) -> Card:
    card = session.get(Card, card_id)
    if card is None:
        raise NotFound(f"no card {card_id}")
    return card


def list_cards(session: Session, project: Project | None = None) -> list[Card]:
    query = select(Card).order_by(Card.id)
    if project is not None:
        query = query.where(Card.project_id == project.id)
    return list(session.scalars(query))


def set_card_status(
    session: Session, card: Card, status: CardStatus, reason: str | None = None
) -> None:
    previous = card.status
    card.status = status  # validated against the card state machine
    card.status_reason = reason
    record_event(
        session,
        "card.status",
        project_id=card.project_id,
        card_id=card.id,
        data={"from": str(previous), "to": str(status), "reason": reason},
    )


def set_run_status(
    session: Session, run: Run, status: RunStatus, reason: str | None = None
) -> None:
    previous = run.status
    run.status = status  # validated against the run state machine
    if reason is not None:
        run.reason = reason
    record_event(
        session,
        "run.status",
        card_id=run.card_id,
        run_id=run.id,
        data={"from": str(previous), "to": str(status), "reason": reason},
    )


def add_comment(
    session: Session,
    card: Card,
    body: str,
    *,
    author: CommentAuthor = CommentAuthor.HUMAN,
    kind: str = "note",
    run_id: int | None = None,
    data: dict[str, Any] | None = None,
) -> Comment:
    comment = Comment(card=card, body=body, author=author, kind=kind, run_id=run_id, data=data)
    session.add(comment)
    session.flush()
    return comment


def latest_human_comment(session: Session, card: Card) -> str | None:
    query = (
        select(Comment)
        .where(Comment.card_id == card.id, Comment.author == CommentAuthor.HUMAN)
        .order_by(Comment.id.desc())
    )
    comment = session.scalars(query).first()
    return comment.body if comment else None


def human_comments_since_last_run(session: Session, card: Card) -> list[Comment]:
    """Human comments posted after the latest run was created (all if no run yet)."""
    run = latest_run(session, card)
    query = (
        select(Comment)
        .where(Comment.card_id == card.id, Comment.author == CommentAuthor.HUMAN)
        .order_by(Comment.id)
    )
    comments = list(session.scalars(query))
    if run is None:
        return comments
    started = as_utc(run.created_at)
    return [c for c in comments if started is None or (as_utc(c.created_at) or started) >= started]


def latest_verdict(session: Session, card: Card) -> Comment | None:
    query = (
        select(Comment)
        .where(
            Comment.card_id == card.id,
            Comment.author == CommentAuthor.REVIEWER,
            Comment.kind == "verdict",
        )
        .order_by(Comment.id.desc())
    )
    return session.scalars(query).first()


def rework_feedback(session: Session, card: Card) -> str | None:
    """What the next run of a reworked card is told: new human comments plus the
    previous reviewer verdict."""
    parts: list[str] = []
    human = human_comments_since_last_run(session, card)
    if human:
        parts.append("From a person reviewing the card:\n" + "\n\n".join(c.body for c in human))
    verdict = latest_verdict(session, card)
    if verdict is not None:
        parts.append("From the previous automated review:\n" + verdict.body)
    return "\n\n".join(parts) or None


def latest_run(session: Session, card: Card) -> Run | None:
    query = select(Run).where(Run.card_id == card.id).order_by(Run.id.desc())
    return session.scalars(query).first()


def events_for_card(session: Session, card: Card) -> list[Event]:
    query = select(Event).where(Event.card_id == card.id).order_by(Event.id)
    return list(session.scalars(query))
