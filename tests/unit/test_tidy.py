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


TESTS = """\
import pytest


def test_adds():
    assert 1 + 1 == 2


def test_subtracts():
    assert 2 - 1 == 1


@pytest.mark.skip(reason="slow")
def test_slow():
    assert True
"""


def test_tests_taken_away_are_found(repo_factory: Callable[..., Path]) -> None:
    repo = repo_factory()
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_math.py").write_text(TESTS, encoding="utf-8")
    (repo / "tests" / "test_old.py").write_text("def test_old():\n    assert True\n", "utf-8")
    (repo / "tests" / "test_moves.py").write_text("def test_moves():\n    assert 1\n", "utf-8")
    (repo / "math.py").write_text("def test_like_name():\n    return 1\n", encoding="utf-8")
    git(repo, "add", "--all")
    git(repo, "commit", "--quiet", "-m", "Tests")
    base = git(repo, "rev-parse", "HEAD")
    assert tidy.test_slips(repo, base) == []

    # A new test, a changed body and a moved file are all fine.
    changed = TESTS.replace("assert 1 + 1 == 2", "assert 2 + 2 == 4")
    (repo / "tests" / "test_math.py").write_text(
        changed + "\n\ndef test_new():\n    assert True\n", encoding="utf-8"
    )
    (repo / "tests" / "unit").mkdir()
    (repo / "tests" / "test_moves.py").rename(repo / "tests" / "unit" / "test_moves.py")
    (repo / "math.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "--all")
    assert tidy.test_slips(repo, base) == []

    # One test removed, one switched off, one file deleted.
    weakened = changed.replace("def test_subtracts():\n    assert 2 - 1 == 1\n", "").replace(
        "def test_adds", "@pytest.mark.skip\ndef test_adds"
    )
    (repo / "tests" / "test_math.py").write_text(weakened, encoding="utf-8")
    (repo / "tests" / "test_old.py").unlink()
    slips = tidy.test_slips(repo, base)
    assert {(s.path, s.what) for s in slips} == {
        ("tests/test_old.py", "deleted"),
        ("tests/test_math.py", "removed"),
        ("tests/test_math.py", "skipped"),
    }
    assert next(s.name for s in slips if s.what == "removed") == "test_subtracts"
    text = tidy.test_feedback(slips)
    assert "tests/test_old.py: test file deleted" in text
    assert "test removed: test_subtracts" in text and "leave it in place" in text


def test_tests_are_told_from_other_code() -> None:
    assert tidy.is_test_path("src/app/Cart.test.tsx") and tidy.is_test_path("pkg/x_test.go")
    assert tidy.is_test_path("tests/Feature/UserTest.php")
    assert not tidy.is_test_path("src/protest/data.py") and not tidy.is_test_path("contest.py")
    assert tidy.test_name("func TestParse(t *testing.T) {") == "TestParse"
    assert tidy.test_name("  it('keeps the total', () => {") == "keeps the total"
    assert tidy.test_name("    total = 1") is None
