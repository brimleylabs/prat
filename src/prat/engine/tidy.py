"""Slips a builder makes with its tools, found in its diff without a model.

Each is something no card asks for and a reviewer then has to send back: a round of review
spent on a typing accident. PRAT finds them as the builder ends its turn and hands them back in
the same session (see ``Engine._finish_check``).

* **Spacing only**: a line whose words are all as they were and whose spacing inside it
  changed. It happens when a builder adds a line next to an aligned one and retypes the
  neighbour to place its edit (``xml         Create...`` comes back as ``xml        Create...``).
  A change of indentation alone is not a slip (code moved into a block), nor is trailing
  whitespace removed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from prat.git import Git

MAX_SLIPS = 12
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class SpacingSlip:
    path: str
    line: int
    before: str
    after: str


def _indent(text: str) -> str:
    return text[: len(text) - len(text.lstrip())]


def is_spacing_slip(before: str, after: str) -> bool:
    """The same words with the same indentation, spaced differently inside the line."""
    old, new = before.rstrip(), after.rstrip()
    return (
        old != new
        and bool(old.strip())
        and _indent(old) == _indent(new)
        and old.split() == new.split()
    )


def spacing_slips(worktree: Path, base: str) -> list[SpacingSlip]:
    """Lines of the work in ``worktree`` (committed or not) that differ from ``base`` only in
    the spacing inside them."""
    diff = Git(worktree).run(
        "diff", "--unified=0", "--no-color", "--no-renames", "--diff-filter=M", base, check=False
    )
    if not diff.ok:
        return []
    found: list[SpacingSlip] = []
    path = ""
    removed: list[str] = []
    added: list[tuple[int, str]] = []
    line = 0

    def close() -> None:
        # A line the hunk takes out that comes back in it with the same words, spaced
        # differently. A line that also comes back unchanged was only moved.
        kept = {after.rstrip() for _, after in added}
        used: set[int] = set()
        for before in removed:
            if before.rstrip() in kept:
                continue
            for number, after in added:
                if number not in used and is_spacing_slip(before, after):
                    used.add(number)
                    found.append(SpacingSlip(path, number, before.rstrip(), after.rstrip()))
                    break
        removed.clear()
        added.clear()

    for text in diff.stdout.splitlines():
        if text.startswith("+++ "):
            close()
            path = text[6:] if text.startswith("+++ b/") else ""
        elif (match := HUNK.match(text)) is not None:
            close()
            line = int(match.group(3))
        elif text.startswith("--- ") or text.startswith("diff --git") or not path:
            continue
        elif text.startswith("-"):
            removed.append(text[1:])
        elif text.startswith("+"):
            added.append((line, text[1:]))
            line += 1
    close()
    return found[:MAX_SLIPS]


def feedback(slips: list[SpacingSlip]) -> str:
    lines = [
        "Before you finish: these lines changed only in their spacing, which no card asks for "
        "(usually a neighbouring line retyped while adding one next to it). Put each back "
        "exactly as it was, unless the card itself asks for the new spacing; then finish again.",
        "",
    ]
    for slip in slips:
        lines += [f"{slip.path}:{slip.line}", f"  was: {slip.before}", f"  now: {slip.after}"]
    return "\n".join(lines)
