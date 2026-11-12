"""Setting up PRAT's own repository as the self project, and keeping it in sync."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from conftest import WRITE_GREET, add_card, git, make_engine, make_repo, project_toml
from prat.config import Settings, load_settings
from prat.db import Database, Project
from prat.db.models import ImproveEvent
from prat.domain import CardStatus
from prat.git import WorktreeManager
from prat.improve.selfproject import (
    SELF_SLUG,
    SelfProjectError,
    check_repo_url,
    find_running_checkout,
    load_self_info,
    self_repo_path,
    setup_self_project,
    sync_self_project,
)

pytestmark = pytest.mark.integration

PACKAGE_INIT = "src/prat/__init__.py"


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A checkout of 'prat' whose origin is a local bare repository."""
    repo = make_repo(
        tmp_path / "src",
        "prat",
        project_toml=project_toml().replace('"acme-api"', '"prat"'),
        files={PACKAGE_INIT: '"""PRAT."""\n'},
    )
    bare = tmp_path / "remote" / "prat.git"
    bare.parent.mkdir(parents=True)
    git(bare.parent, "clone", "-q", "--bare", str(repo), str(bare))
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "fetch", "-q", "origin")
    return repo


def remote_of(source: Path) -> Path:
    return Path(git(source, "config", "remote.origin.url"))


def running_from(source: Path) -> Path:
    return source / PACKAGE_INIT


def commit_to_remote(tmp_path: Path, source: Path, name: str) -> str:
    """Land a commit on the remote's main through a separate clone."""
    pusher = tmp_path / f"pusher-{name}"
    git(tmp_path, "clone", "-q", str(remote_of(source)), str(pusher))
    git(pusher, "config", "user.name", "Test Person")
    git(pusher, "config", "user.email", "person@example.invalid")
    (pusher / f"{name}.txt").write_text(name, encoding="utf-8")
    git(pusher, "add", "-A")
    git(pusher, "commit", "-q", "-m", f"add {name}")
    git(pusher, "push", "-q", "origin", "main")
    return git(pusher, "rev-parse", "HEAD")


def commit_in_clone(clone: Path, name: str) -> str:
    (clone / f"{name}.txt").write_text(name, encoding="utf-8")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", f"add {name}")
    return git(clone, "rev-parse", "HEAD")


def event_kinds(db: Database) -> list[str]:
    with db.session() as session:
        return list(session.scalars(select(ImproveEvent.kind).order_by(ImproveEvent.id)))


def self_project(db: Database) -> Project:
    with db.session() as session:
        project = session.scalars(select(Project).where(Project.is_self.is_(True))).one()
        session.expunge(project)
        return project


# Finding the running checkout ------------------------------------------------------


def test_the_running_checkout_is_found_from_the_package(source: Path) -> None:
    assert find_running_checkout(running_from(source)) == source


def test_a_git_folder_that_does_not_track_prat_is_ignored(tmp_path: Path) -> None:
    host = make_repo(tmp_path / "elsewhere", "other-app")
    installed = host / ".venv" / "site-packages" / "prat" / "__init__.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")
    assert find_running_checkout(installed) is None


def test_no_checkout_at_all(tmp_path: Path) -> None:
    loose = tmp_path / "loose" / "prat" / "__init__.py"
    loose.parent.mkdir(parents=True)
    loose.write_text("", encoding="utf-8")
    assert find_running_checkout(loose) is None


# Setup ----------------------------------------------------------------------------


def test_setup_from_a_url(settings: Settings, db: Database, source: Path) -> None:
    url = str(remote_of(source))
    result = setup_self_project(db, settings, repo_url=url, running_from=running_from(source))
    clone = self_repo_path(settings)
    assert result.created
    assert result.path == clone.resolve()
    assert result.default_branch == "main"
    assert result.head == git(source, "rev-parse", "HEAD")
    assert result.info.push_available

    project = self_project(db)
    assert project.slug == SELF_SLUG
    assert project.name == "prat"
    assert project.checkout_path == project.agent_path == str(clone.resolve())
    assert project.config["name"] == "prat"
    # The clone is an agent clone like any other: nothing can push from it.
    assert WorktreeManager.push_blocked(clone)
    assert git(clone, "config", "remote.origin.url") == url
    with db.session() as session:
        info = load_self_info(session)
    assert info is not None and info.origin == url and info.push_available
    assert event_kinds(db) == ["setup"]


def test_setup_uses_the_running_checkouts_origin(
    settings: Settings, db: Database, source: Path
) -> None:
    result = setup_self_project(db, settings, running_from=running_from(source))
    assert result.info.origin == str(remote_of(source))
    assert result.info.push_available


def test_setup_from_a_checkout_without_a_remote_has_no_push(
    settings: Settings, db: Database, source: Path
) -> None:
    git(source, "remote", "remove", "origin")
    result = setup_self_project(db, settings, running_from=running_from(source))
    assert Path(result.info.origin) == source
    assert not result.info.push_available
    assert "no remote" in result.info.reason
    # The running checkout itself is only read from.
    assert git(source, "status", "--porcelain") == ""


def test_setup_needs_a_url_when_not_running_from_a_checkout(
    settings: Settings, db: Database, tmp_path: Path
) -> None:
    loose = tmp_path / "loose" / "prat" / "__init__.py"
    loose.parent.mkdir(parents=True)
    loose.write_text("", encoding="utf-8")
    with pytest.raises(SelfProjectError, match="pass the repository URL"):
        setup_self_project(db, settings, running_from=loose)
    assert not self_repo_path(settings).exists()


@pytest.mark.parametrize(
    "url",
    [
        "https://bot:hunter2pass@example.invalid/prat.git",
        "ssh://bot:hunter2pass@example.invalid/prat.git",
        "",
        "--upload-pack=evil",
    ],
)
def test_unsafe_urls_are_refused(url: str) -> None:
    with pytest.raises(SelfProjectError):
        check_repo_url(url)


def test_ordinary_urls_are_accepted() -> None:
    for url in (
        "https://example.invalid/prat.git",
        "https://bot@example.invalid/prat.git",
        "git@example.invalid:team/prat.git",
        "ssh://git@example.invalid/prat.git",
    ):
        assert check_repo_url(url) == url


def test_setup_refuses_a_home_inside_the_running_checkout(db: Database, source: Path) -> None:
    inside = load_settings(source / "prat-home")
    with pytest.raises(SelfProjectError, match="never writes into the directory it runs from"):
        setup_self_project(db, inside, running_from=running_from(source))
    assert not self_repo_path(inside).exists()


def test_setup_refuses_a_repository_that_is_not_prat(
    settings: Settings, db: Database, tmp_path: Path
) -> None:
    plain = make_repo(tmp_path / "plain", "plain")
    with pytest.raises(SelfProjectError, match="does not look like PRAT"):
        setup_self_project(db, settings, repo_url=str(plain), running_from=tmp_path / "x.py")
    with db.session() as session:
        assert session.scalars(select(Project)).first() is None
    assert not self_repo_path(settings).exists()


def test_setup_again_updates_the_same_project(
    settings: Settings, db: Database, source: Path, tmp_path: Path
) -> None:
    first = setup_self_project(db, settings, running_from=running_from(source))
    commit_to_remote(tmp_path, source, "later")
    second = setup_self_project(db, settings, running_from=running_from(source), actor="cli")
    assert not second.created
    assert second.project_id == first.project_id
    assert second.head != first.head
    with db.session() as session:
        assert len(list(session.scalars(select(Project)))) == 1
    assert event_kinds(db) == ["setup", "setup"]


def test_setup_refuses_to_overlap_another_project(
    settings: Settings, db: Database, source: Path
) -> None:
    with db.session() as session:
        session.add(
            Project(
                slug="squatter",
                name="squatter",
                checkout_path=str(settings.home / "self"),
                agent_path=str(settings.home / "squatter-agent"),
                default_branch="main",
                config={},
            )
        )
    with pytest.raises(SelfProjectError, match="overlaps paths of project 'squatter'"):
        setup_self_project(db, settings, running_from=running_from(source))


# Sync -----------------------------------------------------------------------------


def test_sync_when_up_to_date(settings: Settings, db: Database, source: Path) -> None:
    setup_self_project(db, settings, running_from=running_from(source))
    result = sync_self_project(db, settings)
    assert result.ok and not result.diverged
    assert "up to date" in result.message
    assert event_kinds(db)[-1] == "sync_ok"


def test_sync_fast_forwards_to_the_remote(
    settings: Settings, db: Database, source: Path, tmp_path: Path
) -> None:
    setup_self_project(db, settings, running_from=running_from(source))
    new_head = commit_to_remote(tmp_path, source, "upstream")
    result = sync_self_project(db, settings)
    assert result.ok and result.head == new_head
    assert git(self_repo_path(settings), "rev-parse", "HEAD") == new_head


def test_sync_with_unpushed_landed_work_is_fine(
    settings: Settings, db: Database, source: Path
) -> None:
    setup_self_project(db, settings, running_from=running_from(source))
    local = commit_in_clone(self_repo_path(settings), "landed")
    result = sync_self_project(db, settings)
    assert result.ok and result.head == local
    assert "not pushed yet" in result.message


def test_sync_reports_divergence_and_changes_nothing(
    settings: Settings, db: Database, source: Path, tmp_path: Path
) -> None:
    setup_self_project(db, settings, running_from=running_from(source))
    clone = self_repo_path(settings)
    local = commit_in_clone(clone, "mine")
    commit_to_remote(tmp_path, source, "theirs")
    result = sync_self_project(db, settings, cycle_id=None)
    assert not result.ok and result.diverged
    assert "diverged" in result.message
    assert git(clone, "rev-parse", "HEAD") == local
    assert event_kinds(db)[-1] == "sync_diverged"


def test_sync_needs_setup_first(settings: Settings, db: Database) -> None:
    with pytest.raises(SelfProjectError, match="not set up"):
        sync_self_project(db, settings)


def test_sync_reports_a_missing_clone(settings: Settings, db: Database, source: Path) -> None:
    from prat.git.worktree import rmtree

    setup_self_project(db, settings, running_from=running_from(source))
    rmtree(self_repo_path(settings))
    result = sync_self_project(db, settings)
    assert not result.ok and not result.diverged
    assert "missing" in result.message
    assert event_kinds(db)[-1] == "sync_failed"


# The engine on the self project ---------------------------------------------------


async def test_a_card_on_the_self_project_runs_and_merges(
    settings: Settings, db: Database, source: Path
) -> None:
    setup_self_project(db, settings, running_from=running_from(source))
    project = self_project(db)
    card_id = add_card(db, project)
    engine, _ = make_engine(settings, db, WRITE_GREET)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW, result.reason

    merged = await engine.approve(card_id)
    clone = self_repo_path(settings)
    assert git(clone, "rev-parse", "main") == merged.commit
    # Merged locally only: the remote and the running checkout are untouched.
    assert git(remote_of(source), "rev-parse", "main") == git(source, "rev-parse", "HEAD")
    assert git(source, "status", "--porcelain") == ""
