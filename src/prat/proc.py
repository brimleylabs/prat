"""Child processes that can be killed together with everything they started.

A gate such as ``python -m pytest`` or ``npm test`` is rarely one process: it
starts workers, shells and servers. When PRAT gives up on a command (timeout
or cancellation) it must take the whole tree down, not just the process it
started, or the leftovers keep running, keep files in the worktree open, and
keep the pipes PRAT is reading from open.

How the tree is held depends on the platform:

* POSIX: the child starts in its own session, so it leads a process group
  that every descendant belongs to. Killing the tree is ``killpg``.
* Windows: the child is created suspended, put in a Job Object, then resumed,
  so every process it creates is in the job from the start. Killing the tree
  is ``TerminateJobObject``. If the job cannot be set up, ``taskkill /T /F``
  walks the tree by parent process id instead.

Nothing here kills a tree that finished on its own: a command that leaves a
background process behind and exits is left alone, on every platform.

A descendant that deliberately detaches (its own session on POSIX) is out of
reach. That needs a sandbox, not a process group.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Self

# How long to wait for output pipes to close after a tree was killed.
KILL_WAIT_SECONDS = 10.0

_File = int | IO[Any] | None

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _CREATE_SUSPENDED = 0x00000004
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_SUSPEND_RESUME = 0x0800
    _TREE_EXIT_CODE = 1

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _ntdll = ctypes.WinDLL("ntdll")
    _ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    _ntdll.NtResumeProcess.restype = ctypes.c_long

    def _create_job() -> int | None:
        """A new, unnamed Job Object, or ``None`` if Windows refuses."""
        handle = _kernel32.CreateJobObjectW(None, None)
        return int(handle) if handle else None

    def _close_handle(handle: int | None) -> None:
        if handle:
            _kernel32.CloseHandle(handle)

    def _adopt_and_resume(job: int, pid: int) -> tuple[bool, bool]:
        """Put a suspended process in the job and let it run.

        Returns ``(in_job, running)``. The process must not be left suspended,
        so the caller kills it when ``running`` is false.
        """
        access = _PROCESS_TERMINATE | _PROCESS_SET_QUOTA | _PROCESS_SUSPEND_RESUME
        handle = _kernel32.OpenProcess(access, False, pid)
        if not handle:
            return False, False
        try:
            in_job = bool(_kernel32.AssignProcessToJobObject(job, handle))
            running = _ntdll.NtResumeProcess(handle) == 0
            return in_job, running
        finally:
            _kernel32.CloseHandle(handle)

    def _taskkill(pid: int) -> None:
        """Kill a process and its descendants by parent process id (the fallback)."""
        root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR")
        program = str(Path(root) / "System32" / "taskkill.exe") if root else "taskkill"
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                [program, "/T", "/F", "/PID", str(pid)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=KILL_WAIT_SECONDS,
                check=False,
            )

    def _start(argv: Sequence[str], options: dict[str, Any]) -> tuple[subprocess.Popen[Any], Any]:
        job = _create_job()
        if job is None:
            return subprocess.Popen(argv, **options), None
        try:
            proc = subprocess.Popen(argv, creationflags=_CREATE_SUSPENDED, **options)
        except BaseException:
            _close_handle(job)
            raise
        in_job, running = _adopt_and_resume(job, proc.pid)
        if not running:
            proc.kill()
            proc.wait()
            _close_handle(job)
            raise OSError(f"could not start {argv[0]!r}: the new process could not be resumed")
        if not in_job:
            # Seen where nested jobs are not allowed. The tree is then killed with taskkill.
            _close_handle(job)
            job = None
        return proc, job

    def _kill(proc: subprocess.Popen[Any], job: Any) -> None:
        if job is not None and _kernel32.TerminateJobObject(job, _TREE_EXIT_CODE):
            return
        if proc.returncode is None:
            _taskkill(proc.pid)
        with contextlib.suppress(OSError):
            proc.kill()

    def _release(job: Any) -> None:
        _close_handle(job)

else:
    import signal

    def _start(argv: Sequence[str], options: dict[str, Any]) -> tuple[subprocess.Popen[Any], Any]:
        # A new session makes the child the leader of a new process group.
        return subprocess.Popen(argv, start_new_session=True, **options), None

    def _kill(proc: subprocess.Popen[Any], job: Any) -> None:
        # Only signal the group while its leader has not been reaped: until then
        # the process id cannot have been given to an unrelated process.
        if proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(OSError):
            proc.kill()

    def _release(job: Any) -> None:
        return None


class ProcessTree:
    """A child process and the means to kill it with all of its descendants."""

    def __init__(self, proc: subprocess.Popen[Any], job: Any = None) -> None:
        self.proc = proc
        self._job = job

    @property
    def contained(self) -> bool:
        """Whether descendants are held by the operating system (not just by parent id)."""
        return sys.platform != "win32" or self._job is not None

    def kill(self) -> None:
        """Kill the process and everything it started. Safe to call more than once."""
        _kill(self.proc, self._job)

    def close(self) -> None:
        """Release what holds the tree. Does not kill anything."""
        job, self._job = self._job, None
        _release(job)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def spawn(
    argv: Sequence[str],
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    stdin: _File = None,
    stdout: _File = None,
    stderr: _File = None,
    text: bool = False,
) -> ProcessTree:
    """Start ``argv`` (never through a shell) so that its whole tree can be killed.

    Raises ``OSError`` if the program cannot be started. With ``text`` the
    pipes are UTF-8 text with undecodable bytes replaced.
    """
    options: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "stdin": stdin,
        "stdout": stdout,
        "stderr": stderr,
    }
    if text:
        options.update(text=True, encoding="utf-8", errors="replace")
    proc, job = _start(list(argv), options)
    return ProcessTree(proc, job)


def _close_pipes(proc: subprocess.Popen[Any]) -> None:
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe is not None:
            with contextlib.suppress(OSError, ValueError):
                pipe.close()


def run_tree(
    argv: Sequence[str],
    *,
    timeout: float,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` to completion and capture its output as UTF-8 text.

    Like ``subprocess.run(..., capture_output=True, timeout=timeout)``, except
    that a timeout kills the whole process tree before
    ``subprocess.TimeoutExpired`` is raised. Killing only the direct child
    would leave its descendants running, and would block here for as long as
    one of them kept the output pipes open. Standard input is inherited unless
    ``input_text`` is given.
    """
    argv = list(argv)
    tree = spawn(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    proc = tree.proc
    try:
        try:
            stdout, stderr = proc.communicate(input_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            tree.kill()
            # Collect what was written. Bounded, in case a descendant escaped the tree.
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.communicate(timeout=KILL_WAIT_SECONDS)
            raise
        except BaseException:
            tree.kill()
            raise
    finally:
        tree.close()
        _close_pipes(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=KILL_WAIT_SECONDS)
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def _windows_started_at(pid: int) -> float | None:
    import ctypes
    from ctypes import wintypes as wt

    # getattr keeps the type check the same on every operating system.
    kernel32: Any = getattr(ctypes, "windll").kernel32  # noqa: B009
    handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        times = [wt.FILETIME() for _ in range(4)]
        code = wt.DWORD()
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None  # 259 is STILL_ACTIVE
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return float(created / 10_000_000 - 11_644_473_600)  # FILETIME counts from 1601
    finally:
        kernel32.CloseHandle(handle)


def _posix_started_at(pid: int) -> float | None:
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
            ticks = int(fields[19])
            boot = next(
                int(line.split()[1])
                for line in Path("/proc/stat").read_text().splitlines()
                if line.startswith("btime ")
            )
            clock: int = getattr(os, "sysconf")("SC_CLK_TCK")  # noqa: B009
            return boot + ticks / clock
        except (OSError, ValueError, IndexError, StopIteration):
            return None
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = result.stdout.strip()
    if result.returncode != 0 or not text:
        return None
    import time

    try:
        return time.mktime(time.strptime(" ".join(text.split()), "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def process_started_at(pid: int) -> float | None:
    """When process ``pid`` started, in seconds since the epoch, or ``None`` if it is not
    running or cannot be read. Used to make sure a remembered process id still names the
    same process before anything is done to it: ids are reused."""
    if pid <= 0:
        return None
    return _windows_started_at(pid) if os.name == "nt" else _posix_started_at(pid)


def kill_pid_tree(pid: int) -> None:
    """Kill a process started by :func:`spawn` and its descendants, by id, when the
    :class:`ProcessTree` is no longer held (PRAT restarted). Check the id first with
    :func:`process_started_at`."""
    if pid <= 0:
        return
    if os.name == "nt":
        root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR")
        program = str(Path(root) / "System32" / "taskkill.exe") if root else "taskkill"
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                [program, "/T", "/F", "/PID", str(pid)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=KILL_WAIT_SECONDS,
                check=False,
            )
        return
    import signal

    # spawn started it in a new session, so it leads a process group with its own id.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        getattr(os, "killpg")(pid, getattr(signal, "SIGKILL"))  # noqa: B009
