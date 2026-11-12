"""Repository instruction files: ``AGENTS.md`` and ``CLAUDE.md``.

Agent sessions run without Claude Code's own settings discovery, on purpose:
no user or project settings, hooks, plugins or memory files are loaded. That
also means the instruction files a repository keeps for coding agents never
reach PRAT's agents. This module is the one deliberate way in.

It reads exactly two file names from the root of the card's worktree, and
nothing else: no search in subdirectories or parent directories, no imports
from inside the files, no settings files, no hooks. The text is handed to
:mod:`prat.engine.prompts`, which puts it in a delimited section and says
that it is the repository's guidance and cannot change PRAT's rules.

A repository controls these files, so they are read defensively:

* The path is resolved first and must still be one of the two files at the
  worktree root. A symlink to anything else (a file outside the worktree, a
  secret file inside it) is refused, so PRAT cannot be made to read some
  other file into a prompt.
* Only regular files are read, and only up to a fixed size.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from prat import paths

log = logging.getLogger(__name__)

# The only names looked for, in the order they are shown to the agent.
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")
# Characters kept from each file. Longer files are cut, with a visible note.
MAX_INSTRUCTION_CHARS = 12_000


@dataclass(frozen=True)
class RepoInstruction:
    """One instruction file as it will be shown to an agent."""

    name: str
    text: str
    truncated: bool = False


def _real_instruction_file(root: Path, name: str) -> Path | None:
    """The resolved path of ``root/name`` if it really is an instruction file at the root."""
    candidate = root / name
    if not os.path.lexists(candidate):
        return None
    resolved = paths.resolve(candidate)
    folded = paths.is_case_insensitive(root)
    allowed = {n.casefold() if folded else n for n in INSTRUCTION_FILES}
    own_name = resolved.name.casefold() if folded else resolved.name
    if own_name not in allowed or not paths.same_path(resolved.parent, root):
        log.warning("not reading %s: it is a link to a file that is not an instruction file", name)
        return None
    # A resolved path is never a link. If resolution gave up (a loop, a link that cannot
    # be read), the link is still there and must not be followed by the read below.
    if resolved.is_symlink() or not resolved.is_file():
        log.warning("not reading %s: it is not a regular file", name)
        return None
    return resolved


def _read_capped(path: Path, max_chars: int) -> tuple[str, bool]:
    """Up to ``max_chars`` characters of a UTF-8 text file, and whether it was cut."""
    with path.open("rb") as handle:
        # A character is at most four bytes, so this is enough to fill the cap and to
        # tell that there was more.
        data = handle.read(max_chars * 4 + 4)
    text = data.decode("utf-8-sig", errors="replace").replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text[:max_chars], len(text) > max_chars


def load_repo_instructions(
    worktree: Path | str, *, max_chars: int = MAX_INSTRUCTION_CHARS
) -> list[RepoInstruction]:
    """The instruction files at the root of ``worktree``. Never raises.

    Files that are missing, empty, unreadable or refused are left out. Two
    names that are the same file (one a link to the other) are shown once.
    """
    root = paths.resolve(worktree)
    found: list[RepoInstruction] = []
    seen: list[Path] = []
    for name in INSTRUCTION_FILES:
        try:
            path = _real_instruction_file(root, name)
            if path is None or any(paths.same_path(path, other) for other in seen):
                continue
            seen.append(path)
            text, truncated = _read_capped(path, max_chars)
        except OSError as exc:
            log.warning("not reading %s: %s", name, type(exc).__name__)
            continue
        if text.strip():
            found.append(RepoInstruction(name, text, truncated))
    return found
