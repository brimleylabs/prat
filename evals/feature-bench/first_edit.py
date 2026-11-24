"""What the winning builder did before its first edit, from the journals of the benchmark's runs.

    python evals/feature-bench/first_edit.py <work root> <case> > first-edit/<machine>-<case>.json

Reads <work root>/<case>-<condition>-<round>/home/journal/run-1.jsonl for the PRAT conditions
on one card. For the builder whose work was kept (PRAT races two) it counts the tool calls made
before the first Edit or Write, the seconds from the start of the build to that edit, the
seconds from that edit to the end of the build, and all its tool calls.

This is a look back at runs already made (ADDENDUM-2.md), not part of the protocol.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

EDITS = {"Edit", "Write"}


def measure(journal: Path) -> dict:
    started = finished = None
    winner = ""
    first: dict[str, float] = {}
    before = {"runtime": 0, "rival": 0}
    calls = {"runtime": 0, "rival": 0}
    for line in journal.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        kind, at = entry["kind"], datetime.fromisoformat(entry["ts"])
        if kind == "race.started":
            started = at
        elif kind == "race.finished":
            finished = at
            winner = "runtime" if entry["data"]["winner"] == "lead" else "rival"
        for who in calls:
            if kind != f"{who}.tool_call":
                continue
            calls[who] += 1
            if who in first:
                continue
            if (entry["data"].get("tool") or entry["data"].get("name")) in EDITS:
                first[who] = (at - started).total_seconds()
            else:
                before[who] += 1
    return {
        "kept": "first builder" if winner == "runtime" else "second builder",
        "calls_before_first_edit": before[winner],
        "seconds_to_first_edit": round(first[winner], 1),
        "seconds_after_first_edit": round((finished - started).total_seconds() - first[winner], 1),
        "tool_calls": calls[winner],
    }


def main() -> None:
    root, case = Path(sys.argv[1]), sys.argv[2]
    found = {}
    for condition in ("single", "single+pack"):
        for journal in sorted(root.glob(f"{case}-{condition}-[0-9]*/home/journal/run-1.jsonl")):
            label = journal.parents[2].name
            found[label] = {"condition": condition, "round": int(label.rsplit("-", 1)[1]), **measure(journal)}
    json.dump(found, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
