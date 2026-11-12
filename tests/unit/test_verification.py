from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from prat.git.worktree import DiffFile, DiffStats
from prat.projects import ProjectSpec
from prat.verification import check_diff, empty_diff_finding, run_command, run_gates
from proc_support import goes_quiet, tree_argv, wait_for_heartbeat

PY = Path(sys.executable).as_posix()


def spec_with(test_code: str, **kwargs: object) -> ProjectSpec:
    return ProjectSpec.model_validate(
        {
            "name": "acme-api",
            "commands": {"test": [PY, "-c", test_code], "lint": [PY, "-c", "print('lint ok')"]},
            "verify": {"gates": ["test", "lint"], "timeout_seconds": 30},
            **kwargs,
        }
    )


def stats(*files: tuple[str, int, int, str]) -> DiffStats:
    return DiffStats(
        base="a" * 40,
        head="b" * 40,
        files=[DiffFile(path, status, added, deleted) for path, added, deleted, status in files],
        commits=[],
    )


def test_gates_pass_and_capture_output(tmp_path: Path) -> None:
    results = run_gates(spec_with("print('tests ok')"), tmp_path, tmp_path / "logs")
    assert [r.name for r in results] == ["test", "lint"]
    assert all(r.passed for r in results)
    assert "tests ok" in results[0].output_tail
    assert Path(results[0].log_path).exists()


def test_gate_failure_stops_the_sequence(tmp_path: Path) -> None:
    results = run_gates(spec_with("import sys; print('boom'); sys.exit(3)"), tmp_path, tmp_path)
    assert len(results) == 1
    assert not results[0].passed
    assert results[0].exit_code == 3
    assert "failed (exit 3)" in results[0].summary
    assert "boom" in results[0].output_tail


def test_gate_timeout(tmp_path: Path) -> None:
    started = time.monotonic()
    results = run_gates(spec_with("import time; time.sleep(30)"), tmp_path, tmp_path, timeout=0.5)
    assert results[0].timed_out
    assert not results[0].passed
    assert time.monotonic() - started < 15


def test_gate_stop_event(tmp_path: Path) -> None:
    stop = threading.Event()
    timer = threading.Timer(0.3, stop.set)
    timer.start()
    code, timed_out, cancelled, _ = run_command(
        [PY, "-c", "import time; time.sleep(30)"],
        tmp_path,
        timeout=30,
        log_path=tmp_path / "x.log",
        stop=stop,
    )
    timer.cancel()
    assert cancelled and not timed_out and code is None


def test_gate_timeout_kills_the_whole_process_tree(tmp_path: Path) -> None:
    """A gate that started other processes must not leave them running after a timeout."""
    heartbeat = tmp_path / "heartbeat"
    argv = tree_argv(heartbeat)
    spec = ProjectSpec.model_validate(
        {
            "name": "acme-api",
            "commands": {"test": argv},
            "verify": {"gates": ["test"], "timeout_seconds": 30},
        }
    )
    results = run_gates(spec, tmp_path, tmp_path / "logs", timeout=4)
    assert results[0].timed_out
    assert heartbeat.exists(), "the grandchild never started, so the test proves nothing"
    assert goes_quiet(heartbeat), "a process started by the gate outlived the timeout"
    assert "parent started" in results[0].output_tail


def test_cancelling_a_gate_kills_the_whole_process_tree(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    stop = threading.Event()
    outcome: list[tuple[int | None, bool, bool, str]] = []
    runner = threading.Thread(
        target=lambda: outcome.append(
            run_command(
                tree_argv(heartbeat), tmp_path, timeout=60, log_path=tmp_path / "x.log", stop=stop
            )
        )
    )
    runner.start()
    try:
        assert wait_for_heartbeat(heartbeat)
    finally:
        stop.set()
        runner.join(timeout=30)
    assert not runner.is_alive()
    assert outcome == [(None, False, True, "")]
    assert goes_quiet(heartbeat), "a process started by the gate outlived the cancellation"


def test_missing_program_reports_error(tmp_path: Path) -> None:
    code, _, _, error = run_command(
        ["prat-no-such-program-xyz"], tmp_path, timeout=5, log_path=tmp_path / "x.log"
    )
    assert code is None
    assert error


def test_empty_diff_is_a_finding() -> None:
    assert empty_diff_finding(stats()) is not None
    assert empty_diff_finding(stats(("a.py", 1, 0, "A"))) is None


def test_oversize_diff() -> None:
    spec = spec_with("pass", max_diff_lines=10, max_diff_files=1)
    findings = check_diff(
        stats(("a.py", 8, 4, "M"), ("b.py", 1, 0, "A")), "", spec, case_insensitive=False
    )
    kinds = [f.kind for f in findings]
    assert kinds.count("oversize_diff") == 2


def test_protected_and_secret_files_in_diff() -> None:
    spec = spec_with("pass", protected_paths=["migrations/**"])
    findings = check_diff(
        stats(
            ("migrations/1.sql", 1, 0, "A"),
            (".prat/project.toml", 1, 1, "M"),
            ("config/.env", 1, 0, "A"),
            (".env.example", 1, 0, "A"),
            ("old.pem", 0, 3, "D"),
        ),
        "",
        spec,
        case_insensitive=False,
    )
    assert {(f.kind, f.path) for f in findings} == {
        ("protected_path", "migrations/1.sql"),
        ("protected_path", ".prat/project.toml"),
        ("secret_file", "config/.env"),
    }


def test_secret_literal_in_added_lines() -> None:
    key = "AKIA" + "M" * 16
    diff = f"+++ b/settings.py\n+KEY = '{key}'\n"
    findings = check_diff(
        stats(("settings.py", 1, 0, "M")), diff, spec_with("pass"), case_insensitive=False
    )
    assert [(f.kind, f.path) for f in findings] == [("secret_literal", "settings.py")]
    assert key not in findings[0].message
