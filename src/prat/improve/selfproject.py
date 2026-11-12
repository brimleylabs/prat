"""The self project: PRAT's own repository, registered as a project.

Self-improvement works on a clone of PRAT's source at
``$PRAT_HOME/self/repo``. There is no separate human working copy: the clone
is both the project's checkout and its agent clone, it is hardened like every
other agent clone (no agent session can push from it), and landed work is
pushed by the engine alone, from a path agent sessions cannot reach.

Where the clone comes from:

* ``repo_url`` given: clone from it. Landed work is pushed there.
* otherwise, when PRAT runs from a git checkout of its own source: that
  checkout's ``origin`` when it has one, else the checkout itself with push
  unavailable until a remote is configured.

PRAT never writes into the directory it is running from: setup refuses when
the clone would land inside that checkout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

import prat
from prat import paths
from prat.config import Settings
from prat.db import Database, Project
from prat.db.models import AppSetting
from prat.git import CloneError, Git, GitError, WorktreeManager
from prat.git.worktree import rmtree
from prat.improve.events import EventKind, record_event
from prat.projects import (
    PROJECT_FILE_REL,
    ProjectFileError,
    ProjectSpec,
    parse_spec,
    show_project_file,
)

SELF_SLUG = "prat-self"
SELF_INFO_KEY = "self_project"
SOURCE_NAME = "the self repository's remote"

# scheme://user:password@host: a password in the URL would be stored in the
# clone's config and in PRAT's database. Credentials belong to git's helper.
_URL_PASSWORD = re.compile(r"^[a-z][a-z0-9+.-]*://[^/@:\s]*:[^/@\s]+@", re.IGNORECASE)


class SelfProjectError(RuntimeError):
    pass


@dataclass(frozen=True)
class SelfInfo:
    """What setup decided, stored in ``app_settings`` under ``self_project``."""

    origin: str
    push_available: bool
    reason: str
    # "core": PRAT's own repository. "overlay": the installation's overlay.
    target: str = "core"

    @property
    def is_overlay(self) -> bool:
        return self.target == "overlay"

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "push_available": self.push_available,
            "reason": self.reason,
            "target": self.target,
        }

    @classmethod
    def from_value(cls, value: Any) -> SelfInfo | None:
        if not isinstance(value, dict):
            return None
        origin, push, reason = value.get("origin"), value.get("push_available"), value.get("reason")
        if not isinstance(origin, str) or not isinstance(push, bool):
            return None
        target = value.get("target") if value.get("target") in ("core", "overlay") else "core"
        return cls(origin, push, reason if isinstance(reason, str) else "", str(target))


@dataclass
class SetupResult:
    project_id: int
    path: Path
    default_branch: str
    head: str
    info: SelfInfo
    created: bool
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SyncResult:
    """The outcome of fetching the remote and fast-forwarding the default branch."""

    ok: bool
    diverged: bool
    head: str | None
    message: str


def self_repo_path(settings: Settings) -> Path:
    return settings.home / "self" / "repo"


def check_repo_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise SelfProjectError("the repository URL is empty")
    if url.startswith("-"):
        raise SelfProjectError(f"invalid repository URL {url!r}")
    if _URL_PASSWORD.match(url):
        raise SelfProjectError(
            "the repository URL contains a password; configure a git credential helper "
            "and pass the URL without it"
        )
    return url


def find_running_checkout(start: Path | None = None) -> Path | None:
    """The git checkout PRAT is running from, or ``None``.

    Walks up from the package. A repository only counts when it tracks the
    package's own files, so a ``.git`` that merely sits above an installed
    copy (a virtual environment inside some other project) is not mistaken
    for PRAT's source.
    """
    package_init = paths.resolve(start or Path(prat.__file__))
    for parent in package_init.parents:
        if not (parent / ".git").exists():
            continue
        git = Git(parent, timeout=30)
        rel = paths.relative_posix(package_init, parent)
        if rel and git.run("ls-files", "--error-unmatch", "--", rel, check=False).ok:
            return parent
        return None
    return None


def load_self_info(session: Session) -> SelfInfo | None:
    row = session.get(AppSetting, SELF_INFO_KEY)
    return SelfInfo.from_value(row.value) if row is not None else None


def get_self_project(session: Session) -> Project | None:
    return session.scalars(select(Project).where(Project.is_self.is_(True))).first()


def _choose_source(repo_url: str | None, running: Path | None) -> SelfInfo:
    if repo_url is not None:
        return SelfInfo(check_repo_url(repo_url), True, "repository URL given at setup")
    if running is None:
        raise SelfProjectError(
            "PRAT is not running from a git checkout of its own source; "
            "pass the repository URL to set up self-improvement"
        )
    origin = Git(running, timeout=30).config_get("remote.origin.url")
    if origin:
        try:
            return SelfInfo(check_repo_url(origin), True, "origin of the running checkout")
        except SelfProjectError as exc:
            raise SelfProjectError(f"the running checkout's origin is not usable: {exc}") from exc
    return SelfInfo(
        str(running),
        False,
        "the running checkout has no remote; landed work stays local until one is configured",
    )


def _default_branch(git: Git) -> str:
    result = git.run("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False)
    if result.ok and result.stdout.strip().startswith("origin/"):
        return result.stdout.strip().removeprefix("origin/")
    branch = git.current_branch()
    if branch:
        return branch
    raise SelfProjectError("could not tell the repository's default branch")


def _spec_on(git: Git, branch: str) -> ProjectSpec:
    shown = show_project_file(git, branch)
    if not shown.ok:
        raise SelfProjectError(
            f"the repository has no {PROJECT_FILE_REL} on {branch}; it does not look like PRAT"
        )
    try:
        return parse_spec(shown.stdout, source=f"{branch}:{PROJECT_FILE_REL}")
    except ProjectFileError as exc:
        raise SelfProjectError(str(exc)) from exc


def _check_location(session: Session, clone: Path, running: Path | None) -> None:
    if running is not None and (paths.is_within(clone, running) or paths.is_within(running, clone)):
        raise SelfProjectError(
            f"the PRAT home is inside PRAT's own checkout ({running}); "
            "PRAT never writes into the directory it runs from, so move the PRAT home elsewhere"
        )
    for other in session.scalars(select(Project).where(Project.is_self.is_(False))):
        for theirs in (other.checkout_path, other.agent_path):
            if paths.is_within(clone, theirs) or paths.is_within(theirs, clone):
                raise SelfProjectError(f"{clone} overlaps paths of project {other.slug!r}")


def overlay_clone_path(settings: Settings) -> Path:
    return settings.home / "self" / "overlay"


def _overlay_source(settings: Settings) -> SelfInfo:
    """Make sure the overlay exists with its project file, committed, and describe it."""
    from prat.overlay import init_overlay, overlay_path

    try:
        init_overlay(
            settings.home,
            author_name=settings.git_author_name,
            author_email=settings.git_author_email,
        )
    except GitError as exc:
        raise SelfProjectError(f"could not prepare the overlay: {exc}") from exc
    return SelfInfo(
        str(paths.resolve(overlay_path(settings.home))),
        True,
        "this installation's overlay; PRAT itself is not changed",
        "overlay",
    )


def setup_self_project(
    db: Database,
    settings: Settings,
    *,
    repo_url: str | None = None,
    running_from: Path | None = None,
    target: str | None = None,
    actor: str = "person",
) -> SetupResult:
    """Create or update the self project and its clone. Safe to run again.

    ``target`` is ``overlay`` (the installation's overlay) or ``core`` (PRAT's own
    source). Without it, a repository URL or ``running_from`` means ``core``, and
    otherwise the ``improve.target`` setting decides. ``running_from`` is the
    package file used to find the running checkout (tests pass their own).
    """
    if target is None and (repo_url is not None or running_from is not None):
        target = "core"
    if target is None:
        from prat.improve.settings import load_improve_settings

        with db.session() as session:
            target = load_improve_settings(session).target
    if target == "overlay":
        running = None
        info = _overlay_source(settings)
        clone = paths.resolve(overlay_clone_path(settings))
    else:
        running = find_running_checkout(running_from)
        info = _choose_source(repo_url, running)
        clone = paths.resolve(self_repo_path(settings))
    with db.session() as session:
        _check_location(session, clone, running)

    worktrees = WorktreeManager(
        settings.worktrees_dir,
        author_name=settings.git_author_name,
        author_email=settings.git_author_email,
    )
    created = False
    git = Git(clone)
    try:
        if clone.exists() and any(clone.iterdir()):
            if not git.is_repo():
                raise SelfProjectError(f"{clone} exists and is not a git repository")
            git.config_set("remote.origin.url", info.origin)
        else:
            clone.parent.mkdir(parents=True, exist_ok=True)
            # The overlay keeps the same bytes for every tool, from the first checkout on.
            options = ["--config", "core.autocrlf=false"] if info.is_overlay else []
            Git(clone.parent).run(
                "clone",
                "--no-hardlinks",
                *options,
                "--origin",
                "origin",
                "--",
                info.origin,
                str(clone),
            )
            created = True
        git.run("remote", "set-head", "origin", "--auto", check=False)
        branch = _default_branch(git)
        refreshed = worktrees.refresh_agent_clone(clone, branch, source=SOURCE_NAME)
        spec = _spec_on(git, branch)
    except (GitError, CloneError, SelfProjectError) as exc:
        if created:
            # A first setup that fails leaves nothing behind for the next attempt to inherit.
            rmtree(clone)
        if isinstance(exc, SelfProjectError):
            raise
        raise SelfProjectError(f"could not prepare the self clone: {exc}") from exc

    with db.session() as session:
        project = get_self_project(session)
        if project is None:
            taken = session.scalars(select(Project).where(Project.slug == SELF_SLUG)).first()
            if taken is not None:
                raise SelfProjectError(f"a project named {SELF_SLUG!r} already exists")
            project = Project(slug=SELF_SLUG, is_self=True)
            session.add(project)
        project.name = spec.name
        project.checkout_path = str(clone)
        project.agent_path = str(clone)
        project.default_branch = branch
        project.config = spec.model_dump(mode="json")
        row = session.get(AppSetting, SELF_INFO_KEY)
        if row is None:
            session.add(AppSetting(key=SELF_INFO_KEY, value=info.to_dict()))
        else:
            row.value = info.to_dict()
        session.flush()
        record_event(
            session,
            EventKind.SETUP,
            f"self project {'created' if created else 'updated'} by {actor}: "
            f"{info.reason}; push {'available' if info.push_available else 'unavailable'}",
            data={
                "created": created,
                "default_branch": branch,
                "head": refreshed.default_head,
                "push_available": info.push_available,
                "actor": actor,
            },
        )
        project_id = project.id
    return SetupResult(
        project_id=project_id,
        path=clone,
        default_branch=branch,
        head=refreshed.default_head,
        info=info,
        created=created,
        warnings=refreshed.warnings,
    )


def sync_self_project(
    db: Database, settings: Settings, *, cycle_id: int | None = None
) -> SyncResult:
    """Fetch the remote and fast-forward the clone's default branch.

    Run before every cycle and every landing. A default branch that has
    diverged from the remote is left alone and reported: nothing may land
    until a person reconciles the two.
    """
    with db.session() as session:
        project = get_self_project(session)
        if project is None:
            raise SelfProjectError("self-improvement is not set up")
        clone, branch = Path(project.agent_path), project.default_branch
    result = _sync(settings, clone, branch)
    kind = (
        EventKind.SYNC_OK
        if result.ok
        else EventKind.SYNC_DIVERGED
        if result.diverged
        else EventKind.SYNC_FAILED
    )
    with db.session() as session:
        record_event(session, kind, result.message, cycle_id=cycle_id, data={"head": result.head})
    return result


def _sync(settings: Settings, clone: Path, branch: str) -> SyncResult:
    git = Git(clone)
    if not git.is_repo():
        return SyncResult(False, False, None, f"the self clone at {clone} is missing")
    try:
        WorktreeManager(
            settings.worktrees_dir,
            author_name=settings.git_author_name,
            author_email=settings.git_author_email,
        ).harden(clone)
        fetched = git.run("fetch", "--quiet", "--prune", "origin", check=False)
        if not fetched.ok:
            detail = fetched.stderr.strip().splitlines()
            return SyncResult(
                False, False, None, f"fetch failed: {detail[-1] if detail else 'no output'}"
            )
        if git.current_branch() != branch:
            if git.is_dirty():
                return SyncResult(False, False, None, "the self clone has uncommitted changes")
            git.run("checkout", "--quiet", branch)
        if git.is_dirty():
            return SyncResult(False, False, None, "the self clone has uncommitted changes")
        remote = f"refs/remotes/origin/{branch}"
        if not git.ref_exists(remote):
            return SyncResult(False, False, git.head(), f"the remote has no branch {branch}")
        local_head, remote_head = git.head(), git.rev_parse(remote)
        if local_head == remote_head:
            return SyncResult(True, False, local_head, f"{branch} is up to date")
        if _is_ancestor(git, local_head, remote_head):
            git.run("merge", "--ff-only", "--quiet", remote)
            head = git.head()
            return SyncResult(True, False, head, f"{branch} fast-forwarded to {head[:10]}")
        if _is_ancestor(git, remote_head, local_head):
            # Landed here and not pushed yet: the next landing pushes it.
            return SyncResult(
                True, False, local_head, f"{branch} is ahead of the remote and not pushed yet"
            )
        return SyncResult(
            False,
            True,
            local_head,
            f"{branch} has diverged from the remote (local {local_head[:10]}, "
            f"remote {remote_head[:10]}); nothing lands until they are reconciled",
        )
    except GitError as exc:
        return SyncResult(False, False, None, f"sync failed: {exc}")


def _is_ancestor(git: Git, ancestor: str, descendant: str) -> bool:
    return git.run("merge-base", "--is-ancestor", ancestor, descendant, check=False).ok
