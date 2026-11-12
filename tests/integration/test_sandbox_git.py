"""Git on this machine must not run anything a container planted in the worktree.

A container can write the whole worktree. Git finds its repository by looking for ``.git``,
so a planted ``.git`` folder with ``core.fsmonitor`` set would make the next git command on
this machine run a program. PRAT's git and the agent's git commands must ignore it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from conftest import git
from prat.git.runner import Git
from prat.sandbox import Sandbox, host_git_config_env

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.name == "nt", reason="planted command written as a POSIX script"),
]


def worktree(tmp_path: Path) -> Path:
    clone = tmp_path / "clone"
    clone.mkdir()
    git(clone, "init", "-q", "-b", "main")
    git(clone, "config", "user.name", "Test Person")
    git(clone, "config", "user.email", "person@example.invalid")
    (clone / "app.py").write_text("x = 1\n", encoding="utf-8")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "start")
    work = tmp_path / "card"
    git(clone, "worktree", "add", "-q", "-b", "card", str(work))
    return work


def plant(folder: Path, marker: Path) -> None:
    """What hostile code in a container could write: a git directory whose settings run a
    program on the next git command."""
    fake = folder / ".git"
    git(folder.parent, "init", "-q", str(folder))
    script = folder / "planted.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    script.chmod(0o755)
    subprocess.run(
        ["git", "--git-dir", str(fake), "config", "core.fsmonitor", str(script)], check=True
    )


def test_a_planted_git_folder_runs_its_program_for_plain_git(tmp_path: Path) -> None:
    work = worktree(tmp_path)
    sub = work / "sub"
    marker = tmp_path / "ran"
    plant(sub, marker)
    subprocess.run(["git", "-C", str(sub), "status"], capture_output=True, check=False)
    assert marker.exists(), "the attack this file guards against no longer works; check git"


def test_prats_git_does_not_run_planted_programs(tmp_path: Path) -> None:
    work = worktree(tmp_path)
    sub = work / "sub"
    marker = tmp_path / "ran"
    plant(sub, marker)
    Git(sub).run("status", check=False)
    Git(work).run("add", "--all", check=False)
    Git(work).run("status", check=False)
    assert not marker.exists()


def test_the_agents_git_is_pinned_to_the_real_repository(tmp_path: Path) -> None:
    work = worktree(tmp_path)
    box = Sandbox.create(
        docker="docker",
        image="unused",
        relay_image="unused",
        name="git-test",
        workdir=work,
        home=tmp_path / "home",
        network=False,
    )
    marker = tmp_path / "ran"
    plant(work / "sub", marker)
    env = {**os.environ, **box.host_git_env()}
    for args in (["status"], ["-C", "sub", "status"], ["-C", "sub", "add", "-A"]):
        subprocess.run(["git", *args], cwd=work, env=env, capture_output=True, check=False)
    assert not marker.exists()
    assert box.tampered() == "sub/.git appeared in the worktree"


def test_a_changed_git_pointer_is_noticed(tmp_path: Path) -> None:
    work = worktree(tmp_path)
    box = Sandbox.create(
        docker="docker",
        image="unused",
        relay_image="unused",
        name="git-test",
        workdir=work,
        home=tmp_path / "home",
        network=False,
    )
    assert box.tampered() is None
    (work / ".git").write_text("gitdir: /somewhere/else\n", encoding="utf-8")
    assert box.tampered() == "the worktree's .git file was changed"


def test_host_git_settings_outrank_the_repositorys(tmp_path: Path) -> None:
    work = worktree(tmp_path)
    git(work, "config", "core.fsmonitor", "/bin/false")
    env = {**os.environ, **host_git_config_env()}
    shown = subprocess.run(
        ["git", "config", "core.fsmonitor"], cwd=work, env=env, capture_output=True, text=True
    )
    assert shown.stdout.strip() == "false"
