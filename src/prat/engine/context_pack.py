"""The files a card is about, read for the builder before it starts.

A builder spends its first turns finding and reading the code a card names. PRAT already has the
card and the repository, so it finds those files itself with one ``git grep`` for the names the
card uses (functions, classes, paths) and puts their current contents in the builder's
instructions, within a size budget. The builder starts with the code in front of it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from prat.git import Git

# Names in a card: snake_case or CamelCase identifiers, and paths.
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:[./][A-Za-z0-9_]+)*")
_COMMON = frozenset(
    [
        "true",
        "false",
        "none",
        "null",
        "self",
        "this",
        "that",
        "with",
        "from",
        "have",
        "will",
        "when",
        "then",
        "them",
        "they",
        "their",
        "there",
        "what",
        "which",
        "while",
        "where",
        "would",
        "should",
        "could",
        "must",
        "also",
        "each",
        "every",
        "only",
        "than",
        "into",
        "over",
        "under",
        "after",
        "before",
        "about",
        "other",
        "another",
        "some",
        "more",
        "most",
        "less",
        "such",
        "same",
        "very",
        "just",
        "like",
        "make",
        "made",
        "test",
        "tests",
        "file",
        "files",
        "code",
        "card",
        "goal",
        "criteria",
        "criterion",
        "added",
        "change",
        "changes",
        "return",
        "returns",
        "value",
        "values",
        "amount",
        "amounts",
        "order",
        "orders",
        "data",
        "list",
        "dict",
        "string",
        "number",
        "name",
        "names",
        "new",
        "old",
    ]
)
_SKIP = ("lock", ".min.", "node_modules/", "vendor/", "dist/", "build/", ".svg", ".map")
MAX_FILE_CHARS = 20_000
MAX_FILES = 16


def card_names(text: str) -> list[str]:
    """The distinct names a card's text uses that could name code, longest first."""
    names: set[str] = set()
    for match in _IDENT.finditer(text):
        word = match.group(0).strip("./")
        if len(word) < 4 or word.lower() in _COMMON:
            continue
        if "_" in word or "/" in word or "." in word or any(c.isupper() for c in word[1:]):
            names.add(word)
    return sorted(names, key=lambda n: (-len(n), n))[:40]


def rank_files(worktree: Path, names: list[str]) -> list[tuple[str, int]]:
    """Tracked files by how many of the card's names they contain, most first."""
    if not names:
        return []
    git = Git(worktree, timeout=30)
    args = ["grep", "-I", "-o", "-F", "--full-name"]
    for name in names:
        args += ["-e", name.split("/")[-1] if "/" in name else name]
    result = git.run(*args, check=False)
    hits: dict[str, set[str]] = defaultdict(set)
    for line in result.stdout.splitlines()[:50_000]:
        path, _, match = line.partition(":")
        if path and not any(s in path for s in _SKIP):
            hits[path].add(match)
    for name in names:
        if "/" in name and (worktree / name).is_file():
            hits[name].add(f"path:{name}")
            hits[name].add(f"path2:{name}")
    ranked = [(path, len(found)) for path, found in hits.items()]
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked


def pack(worktree: Path, card_text: str, budget_chars: int) -> str:
    """The section for the builder's instructions, or "" when nothing fits or matches."""
    if budget_chars <= 0:
        return ""
    try:
        ranked = rank_files(worktree, card_names(card_text))
    except Exception:  # a pack is a convenience; the builder can always read the files
        return ""
    blocks: list[str] = []
    used = 0
    for path, _score in ranked[: MAX_FILES * 2]:
        try:
            text = (worktree / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if len(text) > MAX_FILE_CHARS or used + len(text) > budget_chars:
            continue
        blocks.append(f'<file path="{path}">\n{text.rstrip()}\n</file>')
        used += len(text)
        if len(blocks) >= MAX_FILES:
            break
    if not blocks:
        return ""
    head = (
        "# Files this card is about\n"
        "PRAT found these files from the names the card uses and read them for you. They are "
        "the worktree's current contents: do not read them again before you change them. Read "
        "anything else you need."
    )
    return "\n\n".join([head, *blocks])
