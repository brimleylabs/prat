"""Findings: things a cycle found worth changing in PRAT.

Diagnostics, the roadmap and research all produce :class:`FindingDraft`
objects. :func:`record_findings` stores them by fingerprint, so a problem seen
in every cycle stays one row whose ``seen_count`` and evidence grow, and a
person's decision on it (dismissed) or the loop's (carded, landed, failed)
is never undone by finding it again.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from prat.db.models import ImproveFinding
from prat.domain import Effort, FindingSource, FindingStatus, Severity
from prat.errors import redact_detail, redact_text
from prat.improve.events import EventKind, record_event

MAX_TITLE_CHARS = 200
MAX_TEXT_CHARS = 4000


def finding_fingerprint(source: FindingSource, *parts: str) -> str:
    basis = "\n".join([str(source), *parts])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _cap(text: str, limit: int) -> str:
    text = redact_text(text.strip())
    return text if len(text) <= limit else text[: limit - 3] + "..."


@dataclass(frozen=True)
class FindingDraft:
    source: FindingSource
    fingerprint: str
    title: str
    problem: str
    proposal: str
    acceptance_criteria: Sequence[str]
    severity: Severity = Severity.MEDIUM
    effort: Effort = Effort.MEDIUM
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def criteria_text(self) -> str:
        return "\n".join(f"- {line.strip()}" for line in self.acceptance_criteria if line.strip())


@dataclass(frozen=True)
class Recorded:
    finding_id: int
    fingerprint: str
    new: bool
    status: FindingStatus


def record_findings(
    session: Session,
    drafts: Iterable[FindingDraft],
    *,
    cycle_id: int | None = None,
    now: datetime | None = None,
) -> list[Recorded]:
    """Insert new findings and refresh the ones already known."""
    when = now or datetime.now(UTC)
    out: list[Recorded] = []
    for draft in drafts:
        row = session.scalars(
            select(ImproveFinding).where(ImproveFinding.fingerprint == draft.fingerprint)
        ).first()
        evidence = redact_detail(dict(draft.evidence))
        fields = {
            "title": _cap(draft.title, MAX_TITLE_CHARS),
            "problem": _cap(draft.problem, MAX_TEXT_CHARS),
            "proposal": _cap(draft.proposal, MAX_TEXT_CHARS),
            "acceptance_criteria": _cap(draft.criteria_text(), MAX_TEXT_CHARS),
            "severity": draft.severity,
            "effort": draft.effort,
            "evidence": evidence if isinstance(evidence, dict) else {},
        }
        if row is None:
            row = ImproveFinding(
                cycle_id=cycle_id,
                source=draft.source,
                fingerprint=draft.fingerprint,
                status=FindingStatus.NEW,
                first_seen_at=when,
                last_seen_at=when,
                seen_count=1,
                **fields,
            )
            session.add(row)
            session.flush()
            record_event(
                session,
                EventKind.FINDING_RECORDED,
                f"{draft.source} finding: {row.title}",
                cycle_id=cycle_id,
                data={"finding_id": row.id, "severity": str(draft.severity)},
            )
            out.append(Recorded(row.id, row.fingerprint, True, row.status))
            continue
        for name, value in fields.items():
            setattr(row, name, value)
        row.last_seen_at = when
        row.seen_count += 1
        if row.status == FindingStatus.STALE:
            # It went away and came back.
            row.status = FindingStatus.NEW
        session.flush()
        out.append(Recorded(row.id, row.fingerprint, False, row.status))
    return out


def mark_stale(
    session: Session,
    source: FindingSource,
    seen: Iterable[str],
    *,
    cycle_id: int | None = None,
) -> list[int]:
    """Open findings of ``source`` that were not found this time are stale."""
    keep = set(seen)
    stale: list[int] = []
    rows = session.scalars(
        select(ImproveFinding).where(
            ImproveFinding.source == source, ImproveFinding.status == FindingStatus.NEW
        )
    )
    for row in rows:
        if row.fingerprint in keep:
            continue
        row.status = FindingStatus.STALE
        stale.append(row.id)
        record_event(
            session,
            EventKind.FINDING_STALE,
            f"no longer found: {row.title}",
            cycle_id=cycle_id,
            data={"finding_id": row.id},
        )
    session.flush()
    return stale


def dismiss_finding(
    session: Session, finding_id: int, *, note: str = "", actor: str = "person"
) -> ImproveFinding:
    row = session.get(ImproveFinding, finding_id)
    if row is None:
        raise LookupError(f"no finding {finding_id}")
    if row.status in (FindingStatus.LANDED, FindingStatus.DISMISSED):
        raise ValueError(f"finding {finding_id} is already {row.status}")
    row.status = FindingStatus.DISMISSED
    row.decision_note = _cap(note or f"dismissed by {actor}", MAX_TEXT_CHARS)
    session.flush()
    record_event(
        session,
        EventKind.FINDING_DISMISSED,
        f"dismissed by {actor}: {row.title}",
        card_id=row.card_id,
        data={"finding_id": row.id, "note": row.decision_note, "actor": actor},
    )
    return row


SCREENED_PREFIX = "screened out: "
ALLOWED_PREFIX = "let through by "


def is_screened(row: ImproveFinding) -> bool:
    """Dismissed by the research keyword screen, not by a person."""
    return row.status == FindingStatus.DISMISSED and (row.decision_note or "").startswith(
        SCREENED_PREFIX
    )


def was_let_through(row: ImproveFinding) -> bool:
    return (row.decision_note or "").startswith(ALLOWED_PREFIX)


def allow_screened(session: Session, finding_id: int, *, actor: str = "person") -> ImproveFinding:
    """Put a finding the screen held back into the queue. A person's call: the
    screen is deliberately cautious, and later cycles will not hold it again."""
    row = session.get(ImproveFinding, finding_id)
    if row is None:
        raise LookupError(f"no finding {finding_id}")
    if not is_screened(row):
        raise ValueError(f"finding {finding_id} was not held back by the screen")
    reason = (row.decision_note or "")[len(SCREENED_PREFIX) :]
    row.status = FindingStatus.NEW
    row.decision_note = _cap(f"{ALLOWED_PREFIX}{actor} (screen said: {reason})", MAX_TEXT_CHARS)
    session.flush()
    record_event(
        session,
        EventKind.FINDING_RECORDED,
        f"let through by {actor} after the screen held it ({reason}): {row.title}",
        data={"finding_id": row.id, "actor": actor, "screen": reason},
    )
    return row


def list_findings(
    session: Session,
    *,
    status: FindingStatus | None = None,
    source: FindingSource | None = None,
    limit: int = 200,
) -> list[ImproveFinding]:
    query = select(ImproveFinding).order_by(ImproveFinding.id.desc()).limit(max(1, limit))
    if status is not None:
        query = query.where(ImproveFinding.status == status)
    if source is not None:
        query = query.where(ImproveFinding.source == source)
    return list(session.scalars(query))
