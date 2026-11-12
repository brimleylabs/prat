"""The curator: applies proposed changes to a project's knowledge, without a model.

Changes are small deltas, never a rewrite of the whole (Agentic Context Engineering): add an
entry, refine one, or report one as wrong. The curator rejects anything with a secret, anything
that tries to change PRAT's rules, and anything over the size limits; folds a new entry into an
existing one that says the same thing; supersedes rather than deletes; and never changes an
entry a person wrote.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from prat.improve.research import SCREEN
from prat.knowledge.entries import (
    KINDS,
    MAX_BODY,
    MAX_PATHS,
    MAX_SUMMARY,
    Entry,
    KnowledgeBase,
    Link,
    Source,
    clean_path,
    new_id,
    now_iso,
)
from prat.policy import find_secrets

HELD_FILE = "HELD.json"
# Screen reasons a person may overrule. Anything that looks like a secret is never kept.
HOLDABLE = "asks to "

TOKEN = re.compile(r"[a-z0-9]+")
STOP = {"the", "a", "an", "to", "of", "in", "and", "or", "is", "it", "for", "on", "with", "be"}
SAME_THRESHOLD = 0.55


@dataclass
class Delta:
    op: str  # add, refine, wrong
    kind: str = ""
    summary: str = ""
    body: str = ""
    paths: list[str] = field(default_factory=list)
    id: str = ""
    evidence: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Delta:
        paths = raw.get("paths") or []
        return cls(
            op=str(raw.get("op", "")),
            kind=str(raw.get("kind", "")),
            summary=" ".join(str(raw.get("summary", "")).split()),
            body=str(raw.get("body", "")).strip(),
            paths=[str(p) for p in paths] if isinstance(paths, list) else [],
            id=str(raw.get("id", "")),
            evidence=str(raw.get("evidence", "")).strip(),
        )


@dataclass
class Applied:
    added: list[str] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)
    refined: list[tuple[str, str]] = field(default_factory=list)
    reported: list[str] = field(default_factory=list)
    rejected: list[tuple[Delta, str]] = field(default_factory=list)
    held: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.confirmed or self.refined or self.reported or self.held)

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(f"{len(self.added)} added")
        if self.confirmed:
            parts.append(f"{len(self.confirmed)} confirmed")
        if self.refined:
            parts.append(f"{len(self.refined)} refined")
        if self.reported:
            parts.append(f"{len(self.reported)} reported wrong")
        if self.rejected:
            parts.append(f"{len(self.rejected)} rejected")
        if self.held:
            parts.append(f"{len(self.held)} held for a person")
        return ", ".join(parts) or "no changes"


def tokens(text: str) -> set[str]:
    return {t for t in TOKEN.findall(text.lower()) if t not in STOP}


def similarity(a: Entry, summary: str, body: str) -> float:
    left, right = tokens(a.summary + " " + a.body), tokens(summary + " " + body)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def paths_overlap(first: Sequence[str], second: Sequence[str]) -> bool:
    if not first and not second:
        return True
    for a in first:
        for b in second:
            if a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/"):
                return True
    return False


def find_same(
    entries: Iterable[Entry], summary: str, body: str, paths: Sequence[str]
) -> Entry | None:
    best: tuple[float, Entry] | None = None
    for entry in entries:
        if not entry.active or not paths_overlap(entry.paths, paths):
            continue
        value = similarity(entry, summary, body)
        if value >= SAME_THRESHOLD and (best is None or value > best[0]):
            best = (value, entry)
    return best[1] if best else None


def screen(text: str) -> str | None:
    """Why ``text`` must not become knowledge, or ``None``."""
    if find_secrets(text):
        return "contains something that looks like a secret"
    for reason, pattern in SCREEN:
        if pattern.search(text):
            return reason
    return None


def _check(delta: Delta) -> tuple[list[str], str | None]:
    if delta.kind not in KINDS:
        return [], f"kind must be one of {', '.join(KINDS)}"
    if not delta.summary or len(delta.summary) > MAX_SUMMARY:
        return [], f"the summary must be one line of at most {MAX_SUMMARY} characters"
    if len(delta.body) > MAX_BODY:
        return [], f"the body must be at most {MAX_BODY} characters"
    paths = []
    for raw in delta.paths[:MAX_PATHS]:
        path = clean_path(raw)
        if path is None:
            return [], f"{raw!r} is not a repository path"
        paths.append(path)
    reason = screen(f"{delta.summary}\n{delta.body}")
    return paths, reason


def apply(
    kb: KnowledgeBase,
    deltas: Sequence[Delta],
    *,
    source: Source,
    author: str = "librarian",
    decisive: bool = False,
) -> Applied:
    """Apply deltas to the files. The caller commits. ``decisive`` means the evidence is landed
    code (a card that was approved): one report of an entry being wrong then makes it stale,
    where otherwise it takes reports outweighing the times it helped."""
    result = Applied()
    entries = kb.entries()
    by_id = {entry.id: entry for entry in entries}
    for delta in deltas:
        if delta.op == "add":
            paths, reason = _check(delta)
            if reason:
                if reason.startswith(HOLDABLE):
                    # Possibly a protective fact worded like a weakening one: a person decides.
                    hold(kb, delta, reason, source, author)
                    result.held.append(delta.summary)
                else:
                    result.rejected.append((delta, reason))
                continue
            same = find_same(entries, delta.summary, delta.body, paths)
            if same is not None:
                # Seen again from another card: the same fact, now confirmed. The scout
                # repeating itself on a later look confirms nothing.
                if not (author == "scout" and same.author == "scout"):
                    same.confidence = "confirmed"
                same.last_verified = now_iso()
                same.sources.append(source)
                kb.save(same)
                result.confirmed.append(same.id)
                continue
            entry = Entry(
                id=new_id(),
                kind=delta.kind,
                summary=delta.summary,
                body=delta.body,
                paths=paths,
                author=author,
                confidence="confirmed" if author == "person" else "observed",
                sources=[source],
            )
            kb.save(entry)
            entries.append(entry)
            by_id[entry.id] = entry
            result.added.append(entry.id)
        elif delta.op == "refine":
            old = by_id.get(delta.id)
            if old is None or not old.active:
                result.rejected.append((delta, "no active entry with that id"))
                continue
            if old.author == "person" and author != "person":
                result.rejected.append(
                    (delta, "an entry a person wrote is only changed by a person")
                )
                continue
            delta.kind = delta.kind or old.kind
            paths, reason = _check(delta)
            if reason:
                result.rejected.append((delta, reason))
                continue
            new = Entry(
                id=new_id(),
                kind=delta.kind,
                summary=delta.summary,
                body=delta.body,
                paths=paths or old.paths,
                author=author,
                confidence=old.confidence,
                helped=old.helped,
                links=[
                    Link(old.id, "refines"),
                    *[link for link in old.links if link.relation != "refines"],
                ],
                sources=[*old.sources, source],
            )
            old.status = "superseded"
            old.superseded_by = new.id
            old.invalid_at = now_iso()
            kb.save(new)
            kb.save(old)
            entries.append(new)
            by_id[new.id] = new
            result.refined.append((old.id, new.id))
        elif delta.op == "wrong":
            target = by_id.get(delta.id)
            if target is None or not target.active:
                result.rejected.append((delta, "no active entry with that id"))
                continue
            reason = screen(delta.evidence)
            if reason:
                result.rejected.append((delta, reason))
                continue
            target.hurt += 1
            target.sources.append(
                Source(source.card, source.run, f"reported wrong: {delta.evidence[:300]}")
            )
            if target.author != "person" and (decisive or target.hurt > target.helped + 1):
                target.status = "stale"
                target.invalid_at = now_iso()
            kb.save(target)
            result.reported.append(target.id)
        else:
            result.rejected.append((delta, f"unknown change {delta.op!r}"))
    return result


# Held for a person -------------------------------------------------------------------------
#
# The screen is deliberately blunt: "tests must never run against production" reads like an
# entry about weakening a protection. Instead of loosening the screen (it also guards PRAT's
# self-improvement), such entries are kept in HELD.json in the knowledge repository and shown on
# the Knowledge page, where a person lets each one through or drops it.


def held(kb: KnowledgeBase) -> list[dict[str, Any]]:
    try:
        value = json.loads((kb.root / HELD_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _save_held(kb: KnowledgeBase, items: list[dict[str, Any]]) -> None:
    kb.ensure()
    text = json.dumps(items, indent=2, ensure_ascii=False) + "\n"
    (kb.root / HELD_FILE).write_text(text, encoding="utf-8", newline="\n")


def hold(kb: KnowledgeBase, delta: Delta, reason: str, source: Source, author: str) -> None:
    items = held(kb)
    if any(item.get("summary") == delta.summary for item in items):
        return
    items.append(
        {
            "id": new_id(),
            "kind": delta.kind,
            "summary": delta.summary,
            "body": delta.body,
            "paths": delta.paths,
            "reason": reason,
            "author": author,
            "source": {"card": source.card, "run": source.run, "note": source.note},
            "held_at": now_iso(),
        }
    )
    _save_held(kb, items)


def release(kb: KnowledgeBase, held_id: str, *, accept: bool) -> Entry | None:
    """Let a held entry through as a person's (confirmed, never rewritten), or drop it.
    Returns the new entry when accepted. The caller commits."""
    items = held(kb)
    item = next((i for i in items if i.get("id") == held_id), None)
    if item is None:
        raise LookupError(f"nothing held with id {held_id!r}")
    _save_held(kb, [i for i in items if i is not item])
    if not accept:
        return None
    raw_source = item.get("source") or {}
    entry = Entry(
        id=new_id(),
        kind=str(item.get("kind", "")),
        summary=str(item.get("summary", "")),
        body=str(item.get("body", "")),
        paths=[p for p in (clean_path(str(x)) for x in item.get("paths") or []) if p],
        author="person",
        confidence="confirmed",
        sources=[
            Source(
                raw_source.get("card"),
                raw_source.get("run"),
                f"let through by a person; proposed by the {item.get('author', 'librarian')}",
            )
        ],
    )
    entry.validate()
    kb.save(entry)
    return entry
