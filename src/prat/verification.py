"""Verification of a run's result before it can reach review.

* Gates: the project's own commands (test, lint, build...) run in the card
  worktree with a timeout. Output goes to a log file kept as evidence.
* Empty diff: an execute step that changed nothing is a failure, never a
  success, whatever the agent said.
* Oversize diff: too many changed lines or files fails the run.
* Protected paths: a diff that touches a protected path fails, even if the
  write got past the tool policy some other way.
* Secrets: added lines are scanned for credential-shaped literals.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from prat import paths
from prat.git.worktree import DiffStats
from prat.policy.secrets import scan_diff_additions
from prat.proc import spawn
from prat.projects import (
    PROJECT_DIR,
    SECRET_PATH_EXCEPTIONS,
    ProjectSpec,
    command_argv,
    command_text,
)

OUTPUT_TAIL_CHARS = 4000


@dataclass(frozen=True)
class GateResult:
    name: str
    command: str
    exit_code: int | None
    passed: bool
    timed_out: bool
    cancelled: bool
    duration_seconds: float
    output_tail: str
    log_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def summary(self) -> str:
        if self.cancelled:
            return f"gate {self.name!r} was cancelled"
        if self.timed_out:
            return f"gate {self.name!r} timed out after {self.duration_seconds:.0f}s"
        if self.exit_code is None:
            return f"gate {self.name!r} could not start: {self.output_tail.strip()}"
        state = "passed" if self.passed else f"failed (exit {self.exit_code})"
        return f"gate {self.name!r} {state}"


@dataclass(frozen=True)
class Finding:
    kind: str
    message: str
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _tail(path: Path, chars: int = OUTPUT_TAIL_CHARS) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-chars * 4 :].decode("utf-8", errors="replace")[-chars:]


def run_command(
    argv: Sequence[str],
    cwd: Path,
    *,
    timeout: float,
    log_path: Path,
    stop: threading.Event | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int | None, bool, bool, str]:
    """Run ``argv`` with output to ``log_path``.

    Returns ``(exit_code, timed_out, cancelled, error)``. ``exit_code`` is
    ``None`` when the process could not start or was stopped. On a timeout or
    a stop the whole process tree is killed, on every platform (``prat.proc``).
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    argv = list(argv)
    resolved = shutil.which(argv[0], path=os.environ.get("PATH")) if argv else None
    if resolved is None and argv and (cwd / argv[0]).exists():
        resolved = str(cwd / argv[0])
    if resolved:
        argv[0] = resolved
    with log_path.open("wb") as log:
        try:
            tree = spawn(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            return None, False, False, f"{type(exc).__name__}: {exc}"
        with tree:
            proc = tree.proc
            deadline = time.monotonic() + timeout
            while True:
                code = proc.poll()
                if code is not None:
                    return code, False, False, ""
                cancelled = stop is not None and stop.is_set()
                if cancelled or time.monotonic() >= deadline:
                    # The gate and everything it started: workers, shells, servers.
                    tree.kill()
                    proc.wait()
                    return None, not cancelled, cancelled, ""
                time.sleep(0.05)


# Gates run code the agent wrote; PRAT's own settings and keys stay out of its reach.
PRIVATE_ENV_PREFIXES: tuple[str, ...] = ("PRAT_", "ANTHROPIC_", "CLAUDE_CODE_")


def gate_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """PRAT's environment without its own settings and keys, plus ``extra``."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(PRIVATE_ENV_PREFIXES)
    }
    env.update(extra or {})
    return env


def run_gates(
    spec: ProjectSpec,
    cwd: Path,
    log_dir: Path,
    *,
    names: Sequence[str] | None = None,
    stop: threading.Event | None = None,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
) -> list[GateResult]:
    """Run the configured gates in order, stopping at the first failure.

    ``env`` adds to (or replaces) variables of PRAT's own environment, for
    example to point the project's database at the card's branch.
    """
    results: list[GateResult] = []
    gate_env = gate_environment(env)
    gate_names = list(names) if names is not None else list(spec.verify.gates)
    limit = timeout if timeout is not None else spec.verify.timeout_seconds
    for name in gate_names:
        command = spec.commands.get(name)
        if command is None:
            results.append(
                GateResult(name, "", None, False, False, False, 0.0, "no command defined", "")
            )
            break
        log_path = log_dir / f"gate-{name}.log"
        started = time.monotonic()
        code, timed_out, cancelled, error = run_command(
            command_argv(command), cwd, timeout=limit, log_path=log_path, stop=stop, env=gate_env
        )
        duration = time.monotonic() - started
        output = error or _tail(log_path)
        result = GateResult(
            name=name,
            command=command_text(command),
            exit_code=code,
            passed=code == 0 and not timed_out and not cancelled,
            timed_out=timed_out,
            cancelled=cancelled,
            duration_seconds=round(duration, 3),
            output_tail=output,
            log_path=str(log_path),
        )
        results.append(result)
        if not result.passed:
            break
    return results


def empty_diff_finding(stats: DiffStats) -> Finding | None:
    if stats.empty:
        return Finding("empty_diff", "the run produced no changes")
    return None


def check_diff(
    stats: DiffStats,
    diff_text: str,
    spec: ProjectSpec,
    *,
    case_insensitive: bool,
    project_files: bool = False,
) -> list[Finding]:
    """Size, protected-path and secret checks on a card's diff. ``project_files`` allows
    changes under ``.prat/`` (the setup card's whole job)."""
    findings: list[Finding] = []
    if stats.changed_lines > spec.max_diff_lines:
        findings.append(
            Finding(
                "oversize_diff",
                f"diff changes {stats.changed_lines} lines, limit is {spec.max_diff_lines}",
            )
        )
    if len(stats.files) > spec.max_diff_files:
        findings.append(
            Finding(
                "oversize_diff",
                f"diff touches {len(stats.files)} files, limit is {spec.max_diff_files}",
            )
        )
    protected = [p for p in spec.all_protected if not (project_files and p == f"{PROJECT_DIR}/")]
    for changed in stats.files:
        pattern = paths.matches_any(changed.path, protected, case_insensitive)
        if pattern is not None:
            findings.append(
                Finding(
                    "protected_path", f"{changed.path} matches protected {pattern!r}", changed.path
                )
            )
            continue
        basename = changed.path.rsplit("/", 1)[-1]
        if paths.matches_any(basename, SECRET_PATH_EXCEPTIONS, case_insensitive):
            continue
        secret = paths.matches_any(changed.path, spec.all_secret_paths, case_insensitive)
        if secret is not None and changed.status != "D":
            findings.append(
                Finding("secret_file", f"{changed.path} looks like a secret file", changed.path)
            )
    for file, match in scan_diff_additions(diff_text):
        findings.append(
            Finding("secret_literal", f"{match.kind} on added line ({match.preview})", file)
        )
    return findings
