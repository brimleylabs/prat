"""Adding a project from a repository link: list its branches, clone the one you pick.

PRAT clones into ``projects_dir`` (default ``~/prat-projects``), registers the clone as the
project's checkout with the branch you chose as its default branch, and makes its own agent
clone next to it on the first run, as for any project. git never prompts: a repository that
needs credentials this machine does not have fails with git's own message. Only the https, ssh,
git and file transports are allowed, and a link can never be read as an option.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from prat.config import Settings
from prat.projects import slugify

TIMEOUT_SECONDS = 600
LIST_TIMEOUT_SECONDS = 60
# scp-like ssh (git@github.com:owner/repo.git) or a URL with one of the allowed schemes.
SCP_LIKE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[^\s]+$")
URL_LIKE = re.compile(r"^(https?|ssh|git|file)://[^\s]+$", re.IGNORECASE)


class OnboardError(RuntimeError):
    pass


@dataclass
class Branches:
    names: list[str] = field(default_factory=list)
    default: str | None = None


def is_link(value: str) -> bool:
    """Whether ``value`` is a repository link rather than a path on this machine."""
    text = value.strip()
    return bool(URL_LIKE.match(text) or SCP_LIKE.match(text))


def check_link(value: str) -> str:
    text = value.strip()
    if not is_link(text) or text.startswith("-") or any(c in text for c in "\0\n\r"):
        raise OnboardError("give an https:// or ssh link to a git repository")
    return text


def slug_for(link: str) -> str:
    """A short name from the repository's last path segment."""
    tail = re.split(r"[/:]", link.rstrip("/"))[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    return slugify(tail) or "project"


def projects_dir(settings: Settings) -> Path:
    return (settings.projects_dir or Path.home() / "prat-projects").expanduser()


def _environment() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ALLOW_PROTOCOL"] = "https:http:ssh:git:file"
    env["GIT_SSH_COMMAND"] = env.get("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    return env


def _git(args: list[str], *, timeout: float, cwd: Path | None = None) -> str:
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OnboardError(f"git took longer than {int(timeout)} seconds") from exc
    except OSError as exc:
        raise OnboardError(f"could not run git: {exc}") from exc
    if done.returncode != 0:
        message = (done.stderr or done.stdout).strip().splitlines()
        raise OnboardError(message[-1] if message else f"git failed ({done.returncode})")
    return done.stdout


def list_branches(link: str) -> Branches:
    """The repository's branches and its default branch, read without cloning."""
    url = check_link(link)
    out = _git(
        ["ls-remote", "--symref", "--", url, "HEAD", "refs/heads/*"],
        timeout=LIST_TIMEOUT_SECONDS,
    )
    found = Branches()
    for line in out.splitlines():
        if line.startswith("ref: ") and line.endswith("\tHEAD"):
            ref = line[5:].split("\t", 1)[0]
            found.default = ref.removeprefix("refs/heads/")
            continue
        parts = line.split("\t")
        if len(parts) == 2 and parts[1].startswith("refs/heads/"):
            found.names.append(parts[1].removeprefix("refs/heads/"))
    found.names.sort(key=lambda name: (name != found.default, name.lower()))
    if not found.names:
        raise OnboardError("the repository has no branches yet")
    return found


def clone(link: str, branch: str, destination: Path) -> Path:
    """Clone ``branch`` of ``link`` into ``destination``, which must not exist yet."""
    url = check_link(link)
    if not branch or branch.startswith("-") or any(c in branch for c in " \t\n\\~^:?*["):
        raise OnboardError(f"{branch!r} is not a branch name")
    if destination.exists():
        raise OnboardError(f"{destination} already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _git(
        ["clone", "--quiet", "--branch", branch, "--", url, str(destination)],
        timeout=TIMEOUT_SECONDS,
    )
    return destination
