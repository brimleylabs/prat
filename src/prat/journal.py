"""Append-only JSONL journal, one file per run.

Each line is one JSON object: ``seq``, ``ts`` (UTC, ISO 8601), ``kind`` and
``data``. Strings in ``data`` have credential-shaped values redacted before
they are written (an agent may read a file that holds one), and long strings
are trimmed to a configurable size so a noisy tool cannot bloat the file.
The writer opens, appends and flushes for
every entry, so a crash loses at most the line being written; the reader
skips a truncated final line.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prat.errors import redact_text

TRIM_MARKER = "...[trimmed {n} chars]"


def trim(value: Any, limit: int) -> Any:
    """Recursively trim strings longer than ``limit`` characters."""
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + TRIM_MARKER.format(n=len(value) - limit)
    if isinstance(value, Mapping):
        return {str(k): trim(v, limit) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [trim(v, limit) for v in value]
    if isinstance(value, bool | int | float) or value is None:
        return value
    return trim(str(value), limit)


def scrub(value: Any, limit: int) -> Any:
    """Redact credential-shaped values, then trim. Strings are cut to a bounded
    size before scanning so a huge tool output costs little; anything beyond
    that cut is trimmed away afterwards in any case."""
    if isinstance(value, str):
        clean = redact_text(value[: max(limit, 1) * 4])
        if len(value) <= max(limit, 1) * 4 and len(clean) <= limit:
            return clean
        return clean[:limit] + TRIM_MARKER.format(n=max(len(value) - limit, 0))
    if isinstance(value, Mapping):
        return {str(k): scrub(v, limit) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [scrub(v, limit) for v in value]
    if isinstance(value, bool | int | float) or value is None:
        return value
    return scrub(str(value), limit)


@dataclass(frozen=True)
class JournalEntry:
    seq: int
    ts: datetime
    kind: str
    data: dict[str, Any]


class Journal:
    def __init__(self, path: Path, *, payload_limit: int = 4000) -> None:
        self.path = path
        self.payload_limit = payload_limit
        self._seq: int | None = None

    def _next_seq(self) -> int:
        if self._seq is None:
            self._seq = sum(1 for _ in read_journal(self.path)) if self.path.exists() else 0
        self._seq += 1
        return self._seq

    def append(self, kind: str, data: Mapping[str, Any] | None = None) -> JournalEntry:
        entry = JournalEntry(
            seq=self._next_seq(),
            ts=datetime.now(UTC),
            kind=kind,
            data=scrub(dict(data or {}), self.payload_limit),
        )
        line = json.dumps(
            {"seq": entry.seq, "ts": entry.ts.isoformat(), "kind": entry.kind, "data": entry.data},
            ensure_ascii=False,
            default=str,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return entry


def read_journal(path: Path) -> Iterator[JournalEntry]:
    """Yield entries in order. Malformed lines (a torn final write) are skipped."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            entry = parse_line(line)
            if entry is not None:
                yield entry


def parse_line(line: str) -> JournalEntry | None:
    """One journal line, or ``None`` if it is blank or malformed."""
    line = line.strip()
    if not line:
        return None
    try:
        raw = json.loads(line)
        return JournalEntry(
            seq=int(raw["seq"]),
            ts=datetime.fromisoformat(raw["ts"]),
            kind=str(raw["kind"]),
            data=dict(raw.get("data") or {}),
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def read_first_and_last(
    path: Path, count: int, *, max_bytes: int = 256 * 1024
) -> tuple[JournalEntry | None, list[JournalEntry]]:
    """The first entry and up to ``count`` of the last, reading only the ends of
    the file, so a long journal stays cheap to peek at."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            first = parse_line(handle.readline().decode("utf-8", errors="replace"))
            start = max(0, size - max_bytes)
            handle.seek(start)
            chunk = handle.read(size - start)
    except OSError:
        return None, []
    lines = chunk.split(b"\n")
    if start > 0:
        lines = lines[1:]  # the first line may be cut in half
    entries = [e for e in (parse_line(r.decode("utf-8", errors="replace")) for r in lines) if e]
    return first, entries[-count:] if count > 0 else []


class JournalTail:
    """Reads entries appended to a journal since the last call (for live views)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self._partial = b""

    def read_new(self) -> list[JournalEntry]:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.offset:  # replaced or truncated: start again
            self.offset, self._partial = 0, b""
        if size == self.offset:
            return []
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            chunk = handle.read(size - self.offset)
        self.offset += len(chunk)
        lines = (self._partial + chunk).split(b"\n")
        self._partial = lines.pop()
        entries = []
        for raw in lines:
            entry = parse_line(raw.decode("utf-8", errors="replace"))
            if entry is not None:
                entries.append(entry)
        return entries
