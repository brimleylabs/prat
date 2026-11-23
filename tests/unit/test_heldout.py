"""A test written from the card alone, put in the work, run and taken out (prat.engine.heldout)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from prat.engine import evidence, heldout

PY = f'"{sys.executable}"'
NEEDS_OK = "import pathlib, sys; sys.exit(0 if pathlib.Path('ok.txt').exists() else 1)\n"


def held(path: str = "checks/card_test.py") -> heldout.HeldOut:
    return heldout.HeldOut(7, "abc", path=path, content=NEEDS_OK, command=f"{PY} {path}")


def run(test: heldout.HeldOut, worktree: Path) -> evidence.Ran:
    return heldout.run(
        test, worktree, allowed=lambda command: None, env=dict(os.environ), timeout=30.0
    )


def test_the_answer_is_read_whatever_surrounds_it() -> None:
    answer = 'Here.\n```json\n{"path": "t/test_a.py", "content": "x", "command": "pytest t"}\n```'
    assert heldout.parse(answer) == {
        "path": "t/test_a.py", "content": "x", "command": "pytest t", "why": "",
    }  # fmt: skip
    assert heldout.parse("no json here")["why"] == "no test in the answer"
    assert not heldout.HeldOut(1, "h").usable and held().usable


def test_the_test_runs_on_the_work_and_leaves_nothing_behind(tmp_path: Path) -> None:
    first = run(held(), tmp_path)
    assert first.failed and not (tmp_path / "checks").exists()
    (tmp_path / "ok.txt").write_text("ok\n", encoding="utf-8")
    second = run(held(), tmp_path)
    assert second.exit_code == 0 and sorted(p.name for p in tmp_path.iterdir()) == ["ok.txt"]


def test_a_test_never_overwrites_the_builder_s_file_or_leaves_the_work(tmp_path: Path) -> None:
    # The builder wrote a file of the same name: the test runs beside it under its own.
    (tmp_path / "mine.py").write_text("keep\n", encoding="utf-8")
    beside = run(held("mine.py"), tmp_path)
    assert beside.failed and "mine_heldout7.py" in beside.command
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mine.py"]
    assert (tmp_path / "mine.py").read_text(encoding="utf-8") == "keep\n"
    # A command that does not name the file cannot be pointed at the spare one.
    vague = heldout.HeldOut(7, "abc", path="mine.py", content=NEEDS_OK, command="pytest")
    assert "already there" in run(vague, tmp_path).refused
    assert "outside the work" in run(held("../escape.py"), tmp_path).refused
    assert "outside the work" in run(held(".git/hooks/x.py"), tmp_path).refused


def test_what_the_builder_and_the_reviewer_are_told() -> None:
    failed = evidence.Ran("cmd", 1, output="AssertionError: 2 != 3")
    passed = evidence.Ran("cmd", 0)
    said = heldout.feedback(held(), failed)
    assert "has not seen your work" in said and "AssertionError: 2 != 3" in said
    assert "do not add it" in said and "checks/card_test.py" in said
    assert heldout.review_note(held(), None, None) == ""
    assert "It passes on this work:" in heldout.review_note(held(), passed, None)
    assert "after the builder was shown it failing" in heldout.review_note(held(), failed, passed)
    still = heldout.review_note(held(), failed, failed)
    assert "It still fails" in still and "may be mistaken about the card" in still
    refused = evidence.Ran("cmd", None, refused="not allowed")
    assert "could not be run (not allowed)" in heldout.review_note(held(), refused, None)
