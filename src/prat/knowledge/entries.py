"""Knowledge entries and each project's knowledge repository.

An entry is one fact about a project: a convention, a pitfall, a command, a decision or a
piece of layout. It lives in ``$PRAT_HOME/knowledge/projects/<slug>/entries/<id>.md`` as TOML
front matter between ``+++`` lines and a short body, readable and editable by a person. The
folder is a git repository of its own, so every change to what PRAT believes is a commit.
"""

from __future__ import annotations

import json
import re
import tomllib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from prat.git import Git

# stack: runtimes, frameworks and dependency versions (written by the deep scan).
KINDS = ("convention", "pitfall", "command", "decision", "layout", "stack")
STATUSES = ("active", "stale", "retired", "superseded")
RELATIONS = ("related", "refines", "contradicts")
AUTHORS = ("librarian", "person", "scout", "prat", "checker")
MAX_SUMMARY = 200
MAX_BODY = 1200
MAX_PATHS = 10
INDEX = "MEMORY.md"
ID_RE = re.compile(r"^k[0-9a-f]{10}$")

Kind = Literal["convention", "pitfall", "command", "decision", "layout"]


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def new_id() -> str:
    return "k" + uuid.uuid4().hex[:10]


def clean_path(value: str) -> str | None:
    """A repository path as stored in an entry, or ``None`` if it is not one."""
    text = value.strip().replace("\\", "/").strip("/")
    if not text or text.startswith("..") or "/../" in f"/{text}/" or ":" in text:
        return None
    if PurePosixPath(text).is_absolute():
        return None
    return text


@dataclass
class Link:
    to: str
    relation: str = "related"


@dataclass
class Source:
    card: int | None = None
    run: int | None = None
    note: str = ""


@dataclass
class Entry:
    id: str
    kind: str
    summary: str
    body: str = ""
    paths: list[str] = field(default_factory=list)
    status: str = "active"
    confidence: str = "observed"
    author: str = "librarian"
    helped: int = 0
    hurt: int = 0
    created: str = field(default_factory=now_iso)
    last_verified: str = field(default_factory=now_iso)
    invalid_at: str = ""
    superseded_by: str = ""
    links: list[Link] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.status == "active"

    @property
    def confirmed(self) -> bool:
        return self.confidence == "confirmed"

    def validate(self) -> None:
        """Raise ``ValueError`` if the entry breaks the format's rules."""
        if not ID_RE.match(self.id):
            raise ValueError(f"invalid id {self.id!r}")
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if self.status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        if self.confidence not in ("observed", "confirmed"):
            raise ValueError("confidence must be observed or confirmed")
        if self.author not in AUTHORS:
            raise ValueError(f"author must be one of {', '.join(AUTHORS)}")
        if not self.summary.strip() or len(self.summary) > MAX_SUMMARY or "\n" in self.summary:
            raise ValueError(f"summary must be one line of at most {MAX_SUMMARY} characters")
        if len(self.body) > MAX_BODY:
            raise ValueError(f"body must be at most {MAX_BODY} characters")
        if len(self.paths) > MAX_PATHS or any(clean_path(p) != p for p in self.paths):
            raise ValueError(f"paths must be at most {MAX_PATHS} repository paths")
        for link in self.links:
            if not ID_RE.match(link.to) or link.relation not in RELATIONS:
                raise ValueError(f"invalid link {link.to!r} ({link.relation})")

    # Files ---------------------------------------------------------------------------

    def to_text(self) -> str:
        def q(value: str) -> str:
            # A JSON string is a valid TOML basic string.
            return json.dumps(value, ensure_ascii=False)

        lines = [
            "+++",
            f"id = {q(self.id)}",
            f"kind = {q(self.kind)}",
            f"summary = {q(self.summary)}",
            f"paths = [{', '.join(q(p) for p in self.paths)}]",
            f"status = {q(self.status)}",
            f"confidence = {q(self.confidence)}",
            f"author = {q(self.author)}",
            f"helped = {int(self.helped)}",
            f"hurt = {int(self.hurt)}",
            f"created = {q(self.created)}",
            f"last_verified = {q(self.last_verified)}",
            f"invalid_at = {q(self.invalid_at)}",
            f"superseded_by = {q(self.superseded_by)}",
        ]
        for link in self.links:
            lines += ["", "[[links]]", f"to = {q(link.to)}", f"relation = {q(link.relation)}"]
        for source in self.sources:
            lines += ["", "[[sources]]"]
            if source.card is not None:
                lines.append(f"card = {int(source.card)}")
            if source.run is not None:
                lines.append(f"run = {int(source.run)}")
            lines.append(f"note = {q(source.note)}")
        lines += ["+++", self.body.strip(), ""]
        return "\n".join(lines)

    @classmethod
    def from_text(cls, text: str) -> Entry:
        parts = text.replace("\r\n", "\n").split("+++\n", 2)
        if len(parts) != 3 or parts[0].strip():
            raise ValueError("an entry starts with front matter between +++ lines")
        raw: dict[str, Any] = tomllib.loads(parts[1])
        entry = cls(
            id=str(raw.get("id", "")),
            kind=str(raw.get("kind", "")),
            summary=str(raw.get("summary", "")),
            body=parts[2].strip(),
            paths=[str(p) for p in raw.get("paths", [])],
            status=str(raw.get("status", "active")),
            confidence=str(raw.get("confidence", "observed")),
            author=str(raw.get("author", "librarian")),
            helped=int(raw.get("helped", 0)),
            hurt=int(raw.get("hurt", 0)),
            created=str(raw.get("created", "")),
            last_verified=str(raw.get("last_verified", "")),
            invalid_at=str(raw.get("invalid_at", "")),
            superseded_by=str(raw.get("superseded_by", "")),
            links=[
                Link(str(item.get("to", "")), str(item.get("relation", "related")))
                for item in raw.get("links", [])
            ],
            sources=[
                Source(
                    int(item["card"]) if "card" in item else None,
                    int(item["run"]) if "run" in item else None,
                    str(item.get("note", "")),
                )
                for item in raw.get("sources", [])
            ],
        )
        entry.validate()
        return entry


@dataclass(frozen=True)
class EntryProblem:
    file: str
    reason: str


@dataclass(frozen=True)
class HistoryItem:
    commit: str
    date: str
    subject: str


class KnowledgeBase:
    """One project's knowledge repository."""

    def __init__(self, home: Path, slug: str) -> None:
        self.root = home / "knowledge" / "projects" / slug
        self.slug = slug
        self._cache: tuple[float, list[Entry], list[EntryProblem]] | None = None

    @property
    def entries_dir(self) -> Path:
        return self.root / "entries"

    def ensure(self) -> None:
        """Create the repository if needed. Safe to run again."""
        self.entries_dir.mkdir(parents=True, exist_ok=True)
        git = Git(self.root)
        if not (self.root / ".git").exists():
            git.run("init", "--quiet", "--initial-branch", "main")
        hooks = self.root / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        git.config_set("core.hooksPath", hooks.resolve().as_posix())
        git.config_set("core.autocrlf", "false")
        git.config_set("user.name", "prat")
        git.config_set("user.email", "knowledge@prat.invalid")
        ignore = self.root / ".gitignore"
        if not ignore.exists():
            ignore.write_text(".cache/\n", encoding="utf-8", newline="\n")

    def _stamp(self) -> float:
        try:
            files = list(self.entries_dir.glob("*.md"))
        except OSError:
            return 0.0
        return sum(f.stat().st_mtime_ns for f in files) + len(files)

    def load(self) -> tuple[list[Entry], list[EntryProblem]]:
        """Every entry, and the files that could not be read. Cached until a file changes."""
        stamp = self._stamp()
        if self._cache is not None and self._cache[0] == stamp:
            return list(self._cache[1]), list(self._cache[2])
        entries: list[Entry] = []
        problems: list[EntryProblem] = []
        if self.entries_dir.is_dir():
            for file in sorted(self.entries_dir.glob("*.md")):
                try:
                    entry = Entry.from_text(file.read_text(encoding="utf-8"))
                    if file.stem != entry.id:
                        raise ValueError(f"the file name must be {entry.id}.md")
                    entries.append(entry)
                except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
                    problems.append(EntryProblem(file.name, str(exc)))
        self._cache = (stamp, entries, problems)
        return list(entries), list(problems)

    def entries(self) -> list[Entry]:
        return self.load()[0]

    def get(self, entry_id: str) -> Entry | None:
        return next((e for e in self.entries() if e.id == entry_id), None)

    def save(self, entry: Entry) -> None:
        entry.validate()
        self.ensure()
        path = self.entries_dir / f"{entry.id}.md"
        path.write_text(entry.to_text(), encoding="utf-8", newline="\n")
        self._cache = None

    def write_index(self) -> None:
        """MEMORY.md: one line per active entry, confirmed first, then the newest."""
        active = sorted((e for e in self.entries() if e.active), key=lambda e: e.created)
        active.reverse()  # newest first
        active.sort(key=lambda e: not e.confirmed)  # stable: confirmed first
        lines = [
            f"# What PRAT has learned about {self.slug}",
            "",
            "Written by PRAT. Each line points at one entry in entries/.",
            "",
        ]
        for entry in active:
            mark = "confirmed" if entry.confirmed else "observed"
            where = f" ({', '.join(entry.paths[:3])})" if entry.paths else ""
            lines.append(f"- [{entry.kind}, {mark}] {entry.summary}{where} [{entry.id}]")
        self.ensure()
        (self.root / INDEX).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    def commit(self, message: str) -> str | None:
        """Commit every change. Returns the new commit, or ``None`` if nothing changed."""
        self.ensure()
        git = Git(self.root)
        if not git.is_dirty():
            return None
        git.run("add", "--all")
        git.run("commit", "--quiet", "-m", message)
        return git.head()

    def history(self, entry_id: str | None = None, limit: int = 20) -> list[HistoryItem]:
        if not (self.root / ".git").exists():
            return []
        args = ["log", f"-{max(1, limit)}", "--format=%H%x1f%cI%x1f%s"]
        if entry_id is not None:
            args += ["--", f"entries/{entry_id}.md"]
        result = Git(self.root).run(*args, check=False)
        items = []
        for line in result.stdout.splitlines() if result.ok else []:
            commit, date, subject = [*line.split("\x1f"), "", ""][:3]
            items.append(HistoryItem(commit, date, subject))
        return items

    def cache_dir(self) -> Path:
        path = self.root / ".cache"
        path.mkdir(parents=True, exist_ok=True)
        return path
