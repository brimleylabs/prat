"""Slips found in a diff without a model (prat.engine.tidy)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from conftest import git
from prat.engine import tidy

HELP = """\
    Commands:
        report      Report coverage stats on modules.
        run         Run a Python program and measure code execution.
        xml         Create an XML report of coverage results.
"""


def test_only_spacing_inside_a_line_is_a_slip() -> None:
    assert tidy.is_spacing_slip(
        "    xml         Create an XML report", "    xml        Create an XML report"
    )
    # Moved into a block, trailing spaces dropped, words changed, or untouched: not slips.
    assert not tidy.is_spacing_slip("    total = a + b", "        total = a + b")
    assert not tidy.is_spacing_slip("x = 1   ", "x = 1")
    assert not tidy.is_spacing_slip("x = 1", "x = 2")
    assert not tidy.is_spacing_slip("x = 1", "x = 1")
    assert not tidy.is_spacing_slip("   ", "")


def test_a_neighbour_retyped_while_adding_a_line_is_found(
    repo_factory: Callable[..., Path],
) -> None:
    repo = repo_factory()
    (repo / "cli.py").write_text(HELP + "def f():\n    return 1\n", encoding="utf-8")
    git(repo, "add", "--all")
    git(repo, "commit", "--quiet", "-m", "Help")
    base = git(repo, "rev-parse", "HEAD")
    changed = HELP.replace(
        "        xml         Create",
        "        stale       List changed files.\n        xml        Create",
    )
    # Also a real change, and code moved into a block: neither is reported.
    (repo / "cli.py").write_text(
        changed + "def f():\n    if True:\n        return 2\n", encoding="utf-8"
    )
    slips = tidy.spacing_slips(repo, base)
    assert [(s.path, s.line) for s in slips] == [("cli.py", 5)]
    assert slips[0].before.split() == slips[0].after.split()
    text = tidy.feedback(slips)
    assert "cli.py:5" in text and "was:         xml         Create" in text
    # Put back, nothing is left to say.
    (repo / "cli.py").write_text(
        HELP.replace("        xml  ", "        stale       List.\n        xml  "), encoding="utf-8"
    )
    assert tidy.spacing_slips(repo, base) == []
