"""Path normalisation and matching used by the git layer and the policy engine.

Comparisons resolve symlinks first, then fold case when the filesystem that
holds the path is case-insensitive (the default on Windows and macOS). On a
case-sensitive filesystem paths are compared exactly.
"""

from __future__ import annotations

import contextlib
import functools
import os
import re
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath


class ProtectedPathError(PermissionError):
    """Raised when an operation targets a path PRAT must not touch."""


@functools.lru_cache(maxsize=256)
def _probe_case_insensitive(directory: str) -> bool:
    try:
        fd, name = tempfile.mkstemp(prefix="PratCaseProbe", dir=directory)
    except OSError:
        return sys.platform in ("win32", "darwin")
    try:
        os.close(fd)
        return os.path.exists(os.path.join(directory, os.path.basename(name).lower()))
    finally:
        with contextlib.suppress(OSError):
            os.unlink(name)


def is_case_insensitive(path: str | os.PathLike[str]) -> bool:
    """Best-effort answer for the filesystem that holds ``path``.

    Probes the nearest existing ancestor directory with a temporary file.
    Falls back to the platform default if the probe cannot run.
    """
    candidate = Path(os.path.abspath(path))
    while not candidate.is_dir():
        if candidate.parent == candidate:
            return sys.platform in ("win32", "darwin")
        candidate = candidate.parent
    return _probe_case_insensitive(str(candidate))


def resolve(path: str | os.PathLike[str], base: str | os.PathLike[str] | None = None) -> Path:
    """Absolute path with symlinks resolved (works for paths that do not exist yet)."""
    p = Path(os.path.expanduser(os.fspath(path)))
    if not p.is_absolute():
        p = Path(base if base is not None else os.getcwd()) / p
    return Path(os.path.realpath(p))


def comparable(path: str | os.PathLike[str], case_insensitive: bool | None = None) -> str:
    """A string form of ``path`` suitable for equality and prefix comparison."""
    resolved = resolve(path)
    text = resolved.as_posix().rstrip("/") or "/"
    if case_insensitive is None:
        case_insensitive = is_case_insensitive(resolved)
    return text.casefold() if case_insensitive else text


def is_within(child: str | os.PathLike[str], parent: str | os.PathLike[str]) -> bool:
    """True if ``child`` is ``parent`` or lies beneath it, after resolution."""
    insensitive = is_case_insensitive(parent)
    c = comparable(child, insensitive)
    p = comparable(parent, insensitive)
    if c == p:
        return True
    prefix = p if p.endswith("/") else p + "/"
    return c.startswith(prefix)


def same_path(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    insensitive = is_case_insensitive(a)
    return comparable(a, insensitive) == comparable(b, insensitive)


def assert_outside(
    path: str | os.PathLike[str], protected_roots: Iterable[str | os.PathLike[str]]
) -> None:
    """Raise if ``path`` is inside, equal to, or a parent of any protected root."""
    for root in protected_roots:
        if is_within(path, root) or is_within(root, path):
            raise ProtectedPathError(f"{path} overlaps protected path {root}")


def relative_posix(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> str | None:
    """``path`` relative to ``root`` in POSIX form, or ``None`` if outside root."""
    if not is_within(path, root):
        return None
    full = resolve(path).as_posix()
    base = resolve(root).as_posix().rstrip("/")
    if len(full) <= len(base):
        return ""
    # Casefolding does not change the prefix length for path characters, so
    # slicing keeps the caller's own spelling of the remainder.
    return full[len(base) :].lstrip("/")


@functools.lru_cache(maxsize=1024)
def _glob_regex(pattern: str, case_insensitive: bool) -> re.Pattern[str]:
    """Translate a gitignore-like glob into a regex over POSIX relative paths.

    ``**`` matches across directories, ``*`` and ``?`` stay within one
    segment. A pattern without ``/`` matches the basename at any depth. A
    pattern ending with ``/`` (or a plain directory pattern) also matches
    everything beneath it, so ``dir/**`` protects ``dir`` itself as well.
    """
    pat = pattern.strip().replace("\\", "/")
    anchored = "/" in pat.rstrip("/")
    pat = pat.strip("/")
    while pat.endswith("/**"):
        pat = pat[:-3]
    out = []
    i = 0
    while i < len(pat):
        ch = pat[i]
        if ch == "*":
            if pat[i : i + 2] == "**":
                i += 2
                if pat[i : i + 1] == "/":
                    i += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    body = "".join(out)
    prefix = "" if anchored else "(?:.*/)?"
    # Always match the path itself or anything under it, so "build" protects
    # "build/x" too. That errs on the safe side for a protection list.
    suffix = "(?:/.*)?"
    flags = re.IGNORECASE if case_insensitive else 0
    return re.compile(f"^{prefix}{body}{suffix}$", flags)


def glob_match(rel_path: str, pattern: str, case_insensitive: bool = False) -> bool:
    rel = rel_path.replace("\\", "/").lstrip("/")
    return bool(_glob_regex(pattern, case_insensitive).match(rel))


def matches_any(
    rel_path: str, patterns: Iterable[str], case_insensitive: bool = False
) -> str | None:
    """Return the first pattern that matches ``rel_path``, if any."""
    for pattern in patterns:
        if glob_match(rel_path, pattern, case_insensitive):
            return pattern
    return None


def posix(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).as_posix()
