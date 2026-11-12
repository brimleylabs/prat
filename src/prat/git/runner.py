"""A thin, safe wrapper around the git executable.

Every call uses an argument list (never a shell). Git never prompts for
credentials and output is decoded as UTF-8.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from prat.proc import run_tree

GIT_ENV_DEFAULTS: Mapping[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
    "GCM_INTERACTIVE": "never",
    "LC_ALL": "C",
}
# Errors git reports when it starts while another git process holds or swaps the
# config file. Git has done nothing yet, so trying again is safe.
CONFIG_RACE_ERRORS: tuple[str, ...] = (
    "unknown error occurred while reading the configuration files",
    "could not lock config file",
)
CONFIG_RETRIES = 3


class GitError(RuntimeError):
    def __init__(self, args: Sequence[str], returncode: int, stdout: str, stderr: str) -> None:
        self.git_args = list(args)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        detail = (stderr or stdout).strip().splitlines()
        tail = detail[-1] if detail else ""
        super().__init__(f"git {' '.join(args)} failed ({returncode}): {tail}")


@dataclass(frozen=True)
class GitResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Git:
    """Runs git in a fixed directory."""

    def __init__(self, cwd: Path | str, *, timeout: float = 300.0) -> None:
        self.cwd = Path(cwd)
        self.timeout = timeout

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        # Never let ambient git variables redirect us to another repository.
        for key in list(env):
            if key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR") or (
                key.startswith("GIT_CONFIG_")
            ):
                env.pop(key)
        env.update(GIT_ENV_DEFAULTS)
        return env

    def run(self, *args: str, check: bool = True, input_text: str | None = None) -> GitResult:
        for attempt in range(CONFIG_RETRIES + 1):
            # A timeout kills git together with whatever it started (hooks, helpers).
            proc = run_tree(
                ["git", *args],
                cwd=self.cwd,
                env=self._env(),
                input_text=input_text,
                timeout=self.timeout,
            )
            transient = any(text in proc.stderr for text in CONFIG_RACE_ERRORS)
            if proc.returncode == 0 or not transient or attempt == CONFIG_RETRIES:
                break
            # Git stopped before doing anything: another git process was replacing
            # the shared config file (Windows does not let readers see it mid-swap).
            time.sleep(0.05 * (attempt + 1))
        result = GitResult(list(args), proc.returncode, proc.stdout, proc.stderr)
        if check and proc.returncode != 0:
            raise GitError(args, proc.returncode, proc.stdout, proc.stderr)
        return result

    def out(self, *args: str) -> str:
        return self.run(*args).stdout.strip()

    # Convenience queries -------------------------------------------------

    def rev_parse(self, ref: str) -> str:
        return self.out("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")

    def ref_exists(self, ref: str) -> bool:
        return self.run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).ok

    def branch_exists(self, branch: str) -> bool:
        return self.run("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).ok

    def head(self) -> str:
        return self.out("rev-parse", "HEAD")

    def current_branch(self) -> str | None:
        result = self.run("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        return result.stdout.strip() if result.ok else None

    def is_dirty(self) -> bool:
        return bool(self.out("status", "--porcelain", "--untracked-files=all"))

    def config_get(self, key: str) -> str | None:
        result = self.run("config", "--local", "--get", key, check=False)
        return result.stdout.strip() if result.ok else None

    def config_set(self, key: str, value: str) -> None:
        # Rewriting an unchanged config file only races with other git processes.
        current = self.run("config", "--local", "--get", key, check=False)
        if current.ok and current.stdout.rstrip("\n") == value:
            return
        self.run("config", "--local", key, value)

    def is_repo(self) -> bool:
        if not self.cwd.is_dir():
            return False
        return self.run("rev-parse", "--git-dir", check=False).ok

    def git_common_dir(self) -> Path:
        raw = self.out("rev-parse", "--git-common-dir")
        path = Path(raw)
        return path if path.is_absolute() else (self.cwd / path).resolve()


def check_branch_name(name: str) -> str:
    """Validate a branch name with ``git check-ref-format`` rules."""
    result = subprocess.run(
        ["git", "check-ref-format", "--branch", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or name.startswith("-"):
        raise ValueError(f"invalid branch name {name!r}")
    return name
