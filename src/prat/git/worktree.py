"""Agent clone and per-card worktree management.

Layout for one project::

    checkout_path            the person's own clone (read only to PRAT)
    agent_path               PRAT's clone, default branch checked out
    <worktrees_root>/<slug>/card-<id>   one worktree per active card run

Pushing from the agent clone, or any worktree of it, is made structurally
impossible rather than string-matched:

* ``remote.origin.pushurl`` points at a URL scheme with no transport helper.
* ``url.<sentinel>.pushInsteadOf`` is set to the empty prefix, which rewrites
  every push destination (named remote, path or URL) to the sentinel.
* A ``pre-push`` hook in the shared hooks directory refuses as a last layer.

Worktrees share the clone's config, so all three apply in every worktree.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prat import paths
from prat.git.runner import Git, GitError, check_branch_name
from prat.projects import PROJECT_DIR, check_project_paths, slugify

PUSH_SENTINEL = "prat-push-disabled://"
PRE_PUSH_HOOK = """#!/bin/sh
echo "PRAT: pushing from an agent clone is disabled" >&2
exit 1
"""
# Agents' commits carry no assistant attribution, however the commit was made:
# a session resumed from before the setting changed still writes the trailer.
COMMIT_MSG_HOOK = """#!/bin/sh
grep -v -i -E \\
  '^(co-authored-by:.*(claude|anthropic)|claude-session:|.*generated with \\[claude code\\])' \\
  "$1" > "$1.prat"
mv "$1.prat" "$1"
"""
EXCLUDE_BEGIN = "# >>> PRAT"
EXCLUDE_END = "# <<< PRAT"
DEFAULT_EXCLUDES: tuple[str, ...] = (
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    "*.pyc",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".tox/",
    ".DS_Store",
)


class CloneError(RuntimeError):
    pass


class MergeError(RuntimeError):
    pass


class MergeConflict(MergeError):
    def __init__(self, branch: str, files: list[str]) -> None:
        self.branch = branch
        self.files = files
        super().__init__(f"merge of {branch} conflicts in: {', '.join(files) or 'unknown files'}")


@dataclass
class CloneInfo:
    path: Path
    created: bool
    default_head: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class WorktreeInfo:
    path: Path
    branch: str
    head: str
    created: bool


@dataclass(frozen=True)
class DiffFile:
    path: str
    status: str
    added: int | None
    deleted: int | None

    @property
    def binary(self) -> bool:
        return self.added is None


@dataclass
class DiffStats:
    base: str
    head: str
    files: list[DiffFile]
    commits: list[tuple[str, str]]

    @property
    def insertions(self) -> int:
        return sum(f.added or 0 for f in self.files)

    @property
    def deletions(self) -> int:
        return sum(f.deleted or 0 for f in self.files)

    @property
    def changed_lines(self) -> int:
        return self.insertions + self.deletions

    @property
    def empty(self) -> bool:
        return not self.files

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "head": self.head,
            "files": [f.__dict__ for f in self.files],
            "commits": [list(c) for c in self.commits],
            "insertions": self.insertions,
            "deletions": self.deletions,
        }


def card_branch(card_id: int, title: str) -> str:
    return check_branch_name(f"card/{card_id}-{slugify(title, 40)}")


def _force_remove(func: Callable[..., Any], path: str, exc: BaseException) -> None:
    if isinstance(exc, FileNotFoundError):
        return  # removed meanwhile (by another cleanup): nothing left to do
    os.chmod(path, stat.S_IWRITE)
    func(path)


def rmtree(path: Path) -> None:
    if not path.exists():
        return
    target = str(path)
    if os.name == "nt" and not target.startswith("\\\\?\\"):
        # Installed packages nest past Windows' 260-character path limit; the extended form
        # lets every file be removed instead of leaving a half-deleted worktree behind.
        target = "\\\\?\\" + str(path.resolve())
    shutil.rmtree(target, onexc=_force_remove)


class WorktreeManager:
    def __init__(
        self,
        worktrees_root: Path,
        *,
        author_name: str = "PRAT agent",
        author_email: str = "agent@prat.invalid",
    ) -> None:
        self.worktrees_root = worktrees_root
        self.author_name = author_name
        self.author_email = author_email

    # Agent clone ---------------------------------------------------------

    def prepare_agent_clone(
        self,
        checkout_path: Path,
        agent_path: Path,
        default_branch: str,
        *,
        author: tuple[str, str] | None = None,
    ) -> CloneInfo:
        """Create or refresh the agent clone. Never writes to the checkout.

        ``author`` (name, email) replaces this manager's author for this clone:
        the project's client may have work committed under its own identity.
        """
        checkout = paths.resolve(checkout_path)
        agent = paths.resolve(agent_path)
        check_project_paths(checkout, agent)
        if not Git(checkout).is_repo():
            raise CloneError(f"{checkout} is not a git repository")

        warnings: list[str] = []
        created = False
        if agent.exists() and any(agent.iterdir()):
            git = Git(agent)
            if not git.is_repo():
                raise CloneError(f"{agent} exists and is not a git repository")
            origin = git.config_get("remote.origin.url")
            if origin and not _origin_matches(origin, checkout):
                warnings.append(f"agent clone origin is {origin}, not the checkout")
        else:
            agent.parent.mkdir(parents=True, exist_ok=True)
            Git(agent.parent).run(
                "clone", "--no-hardlinks", "--origin", "origin", "--", str(checkout), str(agent)
            )
            created = True

        info = self.refresh_agent_clone(agent, default_branch, author=author)
        return CloneInfo(agent, created, info.default_head, [*warnings, *info.warnings])

    def refresh_agent_clone(
        self,
        agent_path: Path,
        default_branch: str,
        *,
        source: str = "the checkout",
        author: tuple[str, str] | None = None,
        merge_diverged: bool = True,
    ) -> CloneInfo:
        """Harden an existing agent clone, fetch its origin and bring its work in.

        ``source`` names the origin in messages. The agent clone's default branch
        holds commits of its own (the project file, approved cards), so it often
        cannot fast-forward; with ``merge_diverged`` the origin's new commits are
        merged in, so a card never starts from code older than the person's. A
        merge that conflicts is abandoned and raised: running on stale code is how
        two cards end up with the same migration number. Without it, a diverged
        branch is left as it is and reported as a warning.
        """
        agent = paths.resolve(agent_path)
        git = Git(agent)
        if not git.is_repo():
            raise CloneError(f"{agent} is not a git repository")
        warnings: list[str] = []
        self.harden(agent, author=author)

        fetched = git.run("fetch", "--quiet", "--prune", "origin", check=False)
        if not fetched.ok:
            warnings.append(f"fetch from {source} failed: {fetched.stderr.strip()}")

        if git.current_branch() != default_branch:
            if git.is_dirty():
                raise CloneError(f"agent clone {agent} has uncommitted changes")
            git.run("checkout", "--quiet", default_branch)
        remote_ref = f"refs/remotes/origin/{default_branch}"
        if git.ref_exists(remote_ref) and not git.is_dirty():
            ff = git.run("merge", "--ff-only", "--quiet", remote_ref, check=False)
            if not ff.ok and not merge_diverged:
                warnings.append(
                    f"{default_branch} in the agent clone has diverged from {source}; "
                    "using the agent clone's copy"
                )
            elif not ff.ok:
                merged = git.run(
                    "merge",
                    "--no-edit",
                    "--no-verify",
                    "-m",
                    f"PRAT: bring {source}'s {default_branch} into the agent clone",
                    remote_ref,
                    check=False,
                )
                if not merged.ok:
                    conflicted = git.run("diff", "--name-only", "--diff-filter=U", check=False)
                    files = [f for f in conflicted.stdout.splitlines() if f.strip()]
                    git.run("merge", "--abort", check=False)
                    raise CloneError(
                        f"{default_branch} in the agent clone has commits of its own, and "
                        f"{source}'s new commits conflict with them in: "
                        f"{', '.join(files) or 'unknown files'}. Resolve it in {agent} "
                        f"(merge origin/{default_branch}) before running cards; PRAT will not "
                        "run a card on code older than yours."
                    )
                warnings.append(
                    f"merged {source}'s new commits into the agent clone's {default_branch}"
                )
        return CloneInfo(agent, False, git.head(), warnings)

    def harden(self, agent_path: Path, *, author: tuple[str, str] | None = None) -> None:
        """Apply identity, push blocking and ignore rules. Idempotent."""
        git = Git(agent_path)
        name, email = author or (self.author_name, self.author_email)
        git.config_set("user.name", name)
        git.config_set("user.email", email)
        git.config_set("remote.origin.pushurl", PUSH_SENTINEL)
        git.config_set(f"url.{PUSH_SENTINEL}.pushInsteadOf", "")
        git.config_set("push.default", "nothing")
        common = git.git_common_dir()
        hooks = common / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        # A global core.hooksPath would replace this folder: PRAT's pre-push layer
        # would be skipped and the person's own hooks would run on agents' merges.
        # The clone's own setting wins over the global one.
        git.config_set("core.hooksPath", hooks.resolve().as_posix())
        for name, text in (("pre-push", PRE_PUSH_HOOK), ("commit-msg", COMMIT_MSG_HOOK)):
            hook = hooks / name
            hook.write_text(text, encoding="utf-8", newline="\n")
            hook.chmod(0o755)
        self._write_excludes(common / "info" / "exclude")

    @staticmethod
    def _write_excludes(exclude_file: Path) -> None:
        exclude_file.parent.mkdir(parents=True, exist_ok=True)
        text = exclude_file.read_text(encoding="utf-8") if exclude_file.exists() else ""
        if EXCLUDE_BEGIN in text:
            return
        block = "\n".join([EXCLUDE_BEGIN, *DEFAULT_EXCLUDES, EXCLUDE_END])
        sep = "" if not text or text.endswith("\n") else "\n"
        exclude_file.write_text(f"{text}{sep}{block}\n", encoding="utf-8")

    @staticmethod
    def push_blocked(repo: Path) -> bool:
        git = Git(repo)
        return (
            git.config_get("remote.origin.pushurl") == PUSH_SENTINEL
            and git.run("config", "--get", f"url.{PUSH_SENTINEL}.pushinsteadof", check=False).ok
        )

    # Worktrees -----------------------------------------------------------

    def worktree_path(self, project_slug: str, card_id: int) -> Path:
        return self.worktrees_root / project_slug / f"card-{card_id}"

    def _registered_worktrees(self, agent_path: Path) -> dict[str, str | None]:
        out = Git(agent_path).out("worktree", "list", "--porcelain")
        result: dict[str, str | None] = {}
        current: str | None = None
        for line in out.splitlines():
            if line.startswith("worktree "):
                current = paths.comparable(line[len("worktree ") :])
                result[current] = None
            elif line.startswith("branch ") and current is not None:
                result[current] = line[len("branch ") :].removeprefix("refs/heads/")
        return result

    def create_worktree(
        self, agent_path: Path, path: Path, branch: str, base_ref: str
    ) -> WorktreeInfo:
        """Create (or reuse) the worktree for ``branch`` at ``path``."""
        check_branch_name(branch)
        git = Git(agent_path)
        git.run("worktree", "prune")
        registered = self._registered_worktrees(agent_path)
        key = paths.comparable(path)
        if path.exists():
            if registered.get(key) == branch and Git(path).is_repo():
                return WorktreeInfo(path, branch, Git(path).head(), created=False)
            if key in registered:
                raise CloneError(f"{path} is a worktree for another branch")
            if any(path.iterdir()):
                if not paths.is_within(path, self.worktrees_root):
                    raise CloneError(f"{path} exists and is not a PRAT worktree")
                # PRAT's own folder for this card, left behind by a removal that did not
                # finish (git no longer knows it): clear it and start the worktree again.
                rmtree(path)
            else:
                path.rmdir()
        path.parent.mkdir(parents=True, exist_ok=True)
        if git.branch_exists(branch):
            git.run("worktree", "add", "--quiet", str(path), branch)
        else:
            git.run("worktree", "add", "--quiet", "-b", branch, str(path), base_ref)
        return WorktreeInfo(path, branch, Git(path).head(), created=True)

    def bring_project_files(self, worktree: Path, base_ref: str) -> str | None:
        """Bring an existing card branch up to date when ``.prat/`` on ``base_ref`` differs.

        A card rerun reuses its branch, which may predate a project file or setup script the
        agent needs (and which agents may never write). Merging ``base_ref`` in moves the merge
        base too, so the project files never show up as the card's change. Returns ``"merged"``,
        ``"conflict"`` (the merge was abandoned and the branch left as it was) or ``None`` when
        nothing differed."""
        git = Git(worktree)
        theirs = git.run(
            "rev-parse", "--verify", "--quiet", f"{base_ref}:{PROJECT_DIR}", check=False
        )
        ours = git.run("rev-parse", "--verify", "--quiet", f"HEAD:{PROJECT_DIR}", check=False)
        if not theirs.ok or (ours.ok and ours.stdout.strip() == theirs.stdout.strip()):
            return None
        merged = git.run(
            "merge",
            "--no-edit",
            "--no-verify",
            "-m",
            f"PRAT: bring {base_ref} into the card branch for its current project file",
            base_ref,
            check=False,
        )
        if merged.ok:
            return "merged"
        git.run("merge", "--abort", check=False)
        return "conflict"

    def bring_base(self, worktree: Path, base_ref: str) -> tuple[str | None, list[str]]:
        """Merge ``base_ref`` into the card branch: a swarm part starts each run from the work
        its epic has gathered since. Returns ``(None, [])`` when the branch already has it,
        ``("merged", [])``, or ``("conflict", files)`` with the merge left in progress and the
        conflict markers in those files, for the builder to resolve (PRAT's safety commit then
        completes the merge). A merge that fails without conflicting files is abandoned."""
        git = Git(worktree)
        if git.run("merge-base", "--is-ancestor", base_ref, "HEAD", check=False).ok:
            return None, []
        merged = git.run(
            "merge",
            "--no-edit",
            "--no-verify",
            "-m",
            f"PRAT: bring {base_ref} into the card branch",
            base_ref,
            check=False,
        )
        if merged.ok:
            return "merged", []
        conflicted = git.run("diff", "--name-only", "--diff-filter=U", check=False)
        files = [f for f in conflicted.stdout.splitlines() if f.strip()]
        if not files:
            git.run("merge", "--abort", check=False)
        return "conflict", files

    def remove_worktree(self, agent_path: Path, path: Path) -> None:
        git = Git(agent_path)
        if path.exists():
            git.run("worktree", "remove", "--force", str(path), check=False)
        rmtree(path)
        git.run("worktree", "prune", check=False)

    # Commits and diffs ----------------------------------------------------

    def safety_commit(self, worktree: Path, message: str) -> str | None:
        """Commit anything left uncommitted. Returns the new commit or ``None``."""
        git = Git(worktree)
        git.run("add", "--all")
        if git.run("diff", "--cached", "--quiet", check=False).ok:
            return None
        git.run("commit", "--quiet", "--no-verify", "-m", message)
        return git.head()

    def diff_stats(self, repo: Path, base: str, head: str = "HEAD") -> DiffStats:
        git = Git(repo)
        head_sha = git.rev_parse(head)
        statuses: dict[str, str] = {}
        raw = git.run("diff", "--name-status", "--no-renames", "-z", base, head_sha).stdout
        parts = [p for p in raw.split("\0") if p != ""]
        for status, path in zip(parts[0::2], parts[1::2], strict=False):
            statuses[path] = status
        files: list[DiffFile] = []
        raw = git.run("diff", "--numstat", "--no-renames", "-z", base, head_sha).stdout
        for entry in raw.split("\0"):
            if not entry.strip():
                continue
            added, deleted, path = entry.split("\t", 2)
            files.append(
                DiffFile(
                    path=path,
                    status=statuses.get(path, "M"),
                    added=None if added == "-" else int(added),
                    deleted=None if deleted == "-" else int(deleted),
                )
            )
        log = git.run("log", "--format=%H%x09%s", f"{base}..{head_sha}").stdout
        commits = [
            (line.split("\t", 1)[0], line.split("\t", 1)[1] if "\t" in line else "")
            for line in log.splitlines()
            if line.strip()
        ]
        return DiffStats(base=base, head=head_sha, files=files, commits=commits)

    def diff_text(self, repo: Path, base: str, head: str = "HEAD", context: int = 3) -> str:
        return (
            Git(repo).run("diff", "--no-color", "--no-ext-diff", f"-U{context}", base, head).stdout
        )

    def merge_base(self, repo: Path, a: str, b: str) -> str:
        return Git(repo).out("merge-base", a, b)

    # Merge -----------------------------------------------------------------

    def merge(self, agent_path: Path, branch: str, default_branch: str, message: str) -> str:
        """Merge ``branch`` into the default branch of the agent clone with ``--no-ff``."""
        git = Git(agent_path)
        if git.is_dirty():
            raise MergeError(f"agent clone {agent_path} has uncommitted changes")
        if git.current_branch() != default_branch:
            git.run("checkout", "--quiet", default_branch)
        result = git.run("merge", "--no-ff", "--no-edit", "-m", message, branch, check=False)
        if not result.ok:
            conflicted = git.run("diff", "--name-only", "--diff-filter=U", check=False)
            files = [f for f in conflicted.stdout.splitlines() if f.strip()]
            git.run("merge", "--abort", check=False)
            if files:
                raise MergeConflict(branch, files)
            raise GitError(result.args, result.returncode, result.stdout, result.stderr)
        return git.head()


def _origin_matches(origin: str, checkout: Path) -> bool:
    if "://" in origin and not origin.startswith("file://"):
        return False
    candidate = origin.removeprefix("file://")
    try:
        return paths.same_path(candidate, checkout)
    except OSError:
        return False
