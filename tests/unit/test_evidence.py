"""A reviewer's commands, run to back its rejection (prat.engine.evidence)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from prat.engine import evidence
from prat.engine.critic import parse_review

PY = f'"{sys.executable}"'


def run(commands: list[str], tmp_path: Path, **more: object) -> list[evidence.Ran]:
    options: dict[str, object] = {
        "allowed": lambda command: None,
        "env": dict(os.environ),
        "timeout": 30.0,
        "limit": 3,
        **more,
    }
    return evidence.run(commands, tmp_path, **options)  # type: ignore[arg-type]


def test_a_command_that_fails_backs_a_rejection(tmp_path: Path) -> None:
    ran = run([f"{PY} -c \"import sys; print('boom'); sys.exit(1)\""], tmp_path)
    assert ran[0].exit_code == 1 and ran[0].failed and "boom" in ran[0].output
    assert evidence.backed(ran)


def test_a_command_used_wrongly_says_nothing_about_the_work() -> None:
    usage = evidence.Ran(
        "python -m pytest tests/test_x.py",
        4,
        output="ERROR: usage: __main__.py [options]\nerror: unrecognized arguments: -n",
    )
    nothing = evidence.Ran("python -m pytest tests/test_x.py", 5, output="no tests ran in 0.01s")
    missing = evidence.Ran(
        "jest x", 1, output="'jest' is not recognized as an internal or external command"
    )
    real = evidence.Ran("python -m pytest tests/test_x.py", 1, output="AssertionError: 2 != 3")
    assert usage.misused and nothing.misused and missing.misused and not real.misused
    assert not evidence.backed([usage, nothing, missing]) and evidence.backed([real])
    assert "could not be run (exit 4)" in evidence.note([usage])


def test_commands_that_pass_are_refused_or_cannot_run_back_nothing(tmp_path: Path) -> None:
    ran = run(
        [f'{PY} -c "print(1)"', "rm -rf /", "no-such-command-anywhere --x", "  "],
        tmp_path,
        allowed=lambda command: "not allowed here" if command.startswith("rm") else None,
        limit=3,
    )
    assert [r.exit_code for r in ran[:2]] == [0, None]
    assert ran[1].refused == "not allowed here"
    assert not ran[2].failed or ran[2].exit_code not in evidence.NOT_RUN
    assert not evidence.backed(ran[:2]) and not evidence.backed([])
    said = evidence.note(ran[:2])
    assert "- passed:" in said and "- not run (not allowed here): rm -rf /" in said
    assert "gave no command" in evidence.note([])


def test_only_so_many_commands_run_and_a_slow_one_is_cut_off(tmp_path: Path) -> None:
    quick = f'{PY} -c "print(1)"'
    assert len(run([quick, quick, quick], tmp_path, limit=2)) == 2
    slow = run([f'{PY} -c "import time; time.sleep(20)"'], tmp_path, timeout=1.0)
    assert slow[0].timed_out and slow[0].exit_code is None and not evidence.backed(slow)


def test_when_a_rejection_needs_backing() -> None:
    assert not evidence.needed("any", 5)
    assert evidence.needed("evidence", 1)
    assert not evidence.needed("second", 1) and evidence.needed("second", 2)


def test_a_reviewer_s_commands_are_read_from_its_answer() -> None:
    answer = (
        '```json\n{"verdict": "not_met", "summary": "s", "fix_list": ["fix it"], '
        '"commands": ["pytest tests/test_x.py::test_y -q", ""]}\n```'
    )
    assert parse_review(answer).commands == ["pytest tests/test_x.py::test_y -q"]
    met = '```json\n{"verdict": "met", "summary": "s", "commands": ["pytest"]}\n```'
    assert parse_review(met).commands == []
