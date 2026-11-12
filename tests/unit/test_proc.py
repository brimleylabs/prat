"""Process trees: a command and everything it started die together."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from prat import proc
from prat.proc import run_tree, spawn
from proc_support import goes_quiet, tree_argv, wait_for_heartbeat

PY = sys.executable
WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises the Windows fallback (taskkill); POSIX always uses a process group",
)


def test_run_tree_returns_exit_code_and_output(tmp_path: Path) -> None:
    code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
    result = run_tree([PY, "-c", code], cwd=tmp_path, timeout=30)
    assert result.returncode == 3
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"


def test_run_tree_passes_input_and_environment(tmp_path: Path) -> None:
    code = "import os, sys; print(sys.stdin.read().upper() + os.environ['PRAT_PROC_TEST'])"
    env = {**os.environ, "PRAT_PROC_TEST": "!"}
    result = run_tree([PY, "-c", code], cwd=tmp_path, env=env, input_text="hello", timeout=30)
    assert result.stdout.strip() == "HELLO!"


def test_run_tree_decodes_output_as_utf8(tmp_path: Path) -> None:
    code = "import sys; sys.stdout.buffer.write('caf\\u00e9 \\u6f22'.encode('utf-8') + b' \\xff')"
    result = run_tree([PY, "-c", code], cwd=tmp_path, timeout=30)
    # Two-byte and three-byte characters decode; a stray byte becomes the replacement character.
    assert result.stdout == "caf" + chr(0xE9) + " " + chr(0x6F22) + " " + chr(0xFFFD)


def test_a_missing_program_raises_an_oserror(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_tree(["prat-no-such-program-xyz"], cwd=tmp_path, timeout=5)
    with pytest.raises(FileNotFoundError):
        spawn(["prat-no-such-program-xyz"], cwd=tmp_path)


def test_killing_a_tree_kills_the_processes_it_started(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    with spawn(tree_argv(heartbeat), cwd=tmp_path, stdout=subprocess.DEVNULL) as tree:
        assert tree.contained
        assert wait_for_heartbeat(heartbeat)
        tree.kill()
        assert tree.proc.wait(timeout=30) != 0
        tree.kill()  # a second call is harmless
    assert goes_quiet(heartbeat), "a process started by the command outlived the kill"


def test_run_tree_timeout_kills_the_tree_and_does_not_wait_on_its_pipes(tmp_path: Path) -> None:
    """The heartbeat process holds the output pipes; a direct kill would block on them."""
    heartbeat = tmp_path / "heartbeat"
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_tree(tree_argv(heartbeat), cwd=tmp_path, timeout=4)
    assert time.monotonic() - started < 4 + proc.KILL_WAIT_SECONDS - 2
    assert heartbeat.exists(), "the grandchild never started, so the test proves nothing"
    assert goes_quiet(heartbeat), "a process started by the command outlived the timeout"


def test_a_tree_that_finished_can_still_be_killed_and_closed(tmp_path: Path) -> None:
    tree = spawn([PY, "-c", "print('done')"], cwd=tmp_path, stdout=subprocess.DEVNULL)
    assert tree.proc.wait(timeout=30) == 0
    tree.kill()
    tree.close()
    tree.close()
    tree.kill()
    assert tree.proc.returncode == 0


@WINDOWS_ONLY
def test_without_a_job_object_the_tree_is_killed_through_taskkill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proc, "_create_job", lambda: None)
    heartbeat = tmp_path / "heartbeat"
    with spawn(tree_argv(heartbeat), cwd=tmp_path, stdout=subprocess.DEVNULL) as tree:
        assert not tree.contained
        assert wait_for_heartbeat(heartbeat)
        tree.kill()
        assert tree.proc.wait(timeout=30) != 0
    assert goes_quiet(heartbeat), "a process started by the command outlived taskkill"


@WINDOWS_ONLY
def test_a_process_that_cannot_join_the_job_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Assignment can be refused (nested jobs disallowed). The command must still run."""
    monkeypatch.setattr(proc._kernel32, "AssignProcessToJobObject", lambda job, handle: 0)
    with spawn([PY, "-c", "print('ran')"], cwd=tmp_path, stdout=subprocess.PIPE, text=True) as tree:
        assert not tree.contained
        out, _ = tree.proc.communicate(timeout=30)
    assert out.strip() == "ran"
    assert tree.proc.returncode == 0
