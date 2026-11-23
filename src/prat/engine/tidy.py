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


# Tests ---------------------------------------------------------------------------------------

TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|specs?)/"
    r"|(^|/)test_[^/]+\.py$|_test\.(py|go|rb|exs?)$|_spec\.rb$"
    r"|\.(test|spec)\.[cm]?[jt]sx?$|Tests?\.(php|java|kt|cs|swift)$"
)
# The name of a test a line declares, whatever the language.
TEST_NAME = re.compile(
    r"^\s*(?:async\s+)?def\s+(test\w*)"
    r"|^\s*func\s+(Test\w+)"
    r"|^\s*(?:public\s+|final\s+)*function\s+(test\w+)"
    r"|^\s*(?:public\s+|internal\s+)?(?:async\s+)?(?:void|Task|fun)\s+(\w*[Tt]est\w*)\s*\("
    r"|^\s*(?:it|test|specify|scenario)(?:\.\w+)*\s*\(\s*(['\"`])(?P<title>.+?)\5"
    r"|^\s*(?:it|test|specify|scenario)\s+(['\"])(?P<title2>.+?)\7"
)
SKIP = re.compile(
    r"@pytest\.mark\.(skip|skipif|xfail)|@unittest\.(skip|expectedFailure)|\bpytest\.skip\("
    r"|\b(it|test|describe|context)\.(skip|todo)\b|\bx(it|describe|test|context)\s*\("
    r"|\bt\.Skip(f|Now)?\(|markTest(Skipped|Incomplete)\(|@(Disabled|Ignore)\b|#\[ignore\]"
    r"|\bskip\s*\(|\[(Ignore|Skip)\b"
)


@dataclass(frozen=True)
class TestSlip:
    __test__ = False  # not a test, whatever pytest makes of the name

    path: str
    # "deleted": the test file is gone. "removed": a test in it is. "skipped": one was switched off.
    what: str
    name: str = ""


def is_test_path(path: str) -> bool:
    return TEST_PATH.search(path.replace("\\", "/")) is not None


def test_name(line: str) -> str | None:
    match = TEST_NAME.match(line)
    if match is None:
        return None
    return next((group for group in match.groups() if group and len(group) > 1), None)


def test_slips(worktree: Path, base: str) -> list[TestSlip]:
    """Tests that existed at ``base`` and that the work in ``worktree`` (committed or not)
    deletes, removes or switches off. A test file that only moved is not one."""
    git = Git(worktree)
    names = git.run("diff", "--name-status", "--find-renames", "--no-color", base, check=False)
    if not names.ok:
        return []
    found = [
        TestSlip(path, "deleted")
        for status, _, path in (line.partition("\t") for line in names.stdout.splitlines())
        if status == "D" and is_test_path(path)
    ]
    diff = git.run(
        "diff", "--unified=0", "--no-color", "--find-renames", "--diff-filter=MR", base, check=False
    )
    if not diff.ok:
        return found[:MAX_SLIPS]
    path = ""
    removed: list[str] = []
    added: list[str] = []

    def close() -> None:
        if path and is_test_path(path):
            still = {name for name in map(test_name, added) if name}
            for name in dict.fromkeys(name for name in map(test_name, removed) if name):
                if name not in still:
                    found.append(TestSlip(path, "removed", name))
            had = sum(1 for line in removed if SKIP.search(line))
            for line in added:
                if SKIP.search(line):
                    if had:
                        had -= 1  # a skip that was already there, moved or reworded
                    else:
                        found.append(TestSlip(path, "skipped", line.strip()[:120]))
        removed.clear()
        added.clear()

    for text in diff.stdout.splitlines():
        if text.startswith("+++ "):
            close()
            path = text[6:] if text.startswith("+++ b/") else ""
        elif text.startswith("--- ") or text.startswith("diff --git") or text.startswith("@@"):
            continue
        elif text.startswith("-"):
            removed.append(text[1:])
        elif text.startswith("+"):
            added.append(text[1:])
    close()
    return found[:MAX_SLIPS]


def test_feedback(slips: list[TestSlip]) -> str:
    said = {
        "deleted": "test file deleted",
        "removed": "test removed",
        "skipped": "test switched off",
    }
    lines = [
        "Before you finish: this work takes away tests that were here when you started. Put "
        "each back as it was, unless the card itself asks for it to go. If one of them is wrong, "
        "or cannot pass with what the card asks for, leave it in place and say so in your final "
        "message, naming the test and why, so a person decides; then finish again.",
        "",
    ]
    for slip in slips:
        lines.append(f"{slip.path}: {said[slip.what]}{': ' + slip.name if slip.name else ''}")
    return "\n".join(lines)
