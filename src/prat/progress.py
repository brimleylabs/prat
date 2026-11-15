"""Report a job's progress to PRAT from Python. Any language can do the same by appending one
JSON line per update to the file named in ``PRAT_PROGRESS``::

    {"ts": 1763150000.0, "phase": "summarise", "done": 4212, "total": 78274, "errors": 31,
     "unit": "campgrounds", "item": "Moab KOA", "tokens_in": 1200, "tokens_out": 300,
     "model": "some-model"}

Every field is optional except ``done``. ``ts`` (seconds since the epoch) makes rate and ETA
exact; without it PRAT uses the time it read the line. Outside PRAT (no ``PRAT_PROGRESS``)
reporting does nothing, so a job runs the same by hand.

    from prat.progress import Progress

    progress = Progress(total=len(rows), phase="summarise", unit="campgrounds")
    for row in rows:
        ...
        progress.advance(item=row.name)
    progress.finish()
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

VARIABLE = "PRAT_PROGRESS"


class Progress:
    def __init__(
        self,
        total: float | None = None,
        *,
        phase: str = "",
        unit: str = "items",
        every_seconds: float = 1.0,
        path: str | os.PathLike[str] | None = None,
    ) -> None:
        target = path or os.environ.get(VARIABLE)
        self.path = Path(target) if target else None
        self.total = total
        self.phase_name = phase
        self.unit = unit
        self.done = 0.0
        self.errors = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.model = ""
        self.every = every_seconds
        self._last = 0.0
        self._item = ""

    def phase(self, name: str, total: float | None = None) -> None:
        """Start a new phase: done starts again from zero."""
        self.phase_name = name
        self.total = total
        self.done = 0
        self._write(force=True)

    def advance(self, n: float = 1, *, item: str = "", errors: int = 0, note: str = "") -> None:
        self.done += n
        self.errors += errors
        self._item = item or self._item
        self._write(note=note)

    def error(self, n: int = 1, note: str = "") -> None:
        self.errors += n
        self._write(note=note)

    def tokens(self, tokens_in: int = 0, tokens_out: int = 0, model: str = "") -> None:
        """Model usage the job spends itself (its own LLM calls)."""
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.model = model or self.model

    def finish(self, note: str = "") -> None:
        self._write(force=True, note=note)

    def _write(self, *, force: bool = False, note: str = "") -> None:
        if self.path is None:
            return
        now = time.time()
        if not force and now - self._last < self.every and not note:
            return
        self._last = now
        line: dict[str, Any] = {"ts": now, "done": self.done, "unit": self.unit}
        for key, value in (
            ("phase", self.phase_name),
            ("total", self.total),
            ("errors", self.errors),
            ("item", self._item),
            ("note", note),
            ("tokens_in", self.tokens_in),
            ("tokens_out", self.tokens_out),
            ("model", self.model),
        ):
            if value:
                line[key] = value
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
