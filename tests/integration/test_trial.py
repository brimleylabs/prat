"""The staged trial, run for real against candidates built from prat's own source."""

from __future__ import annotations

import socket
import subprocess
import threading
from pathlib import Path

import pytest
from sqlalchemy import select

from conftest import git
from prat.config import Settings
from prat.db import Database
from prat.db.migrate import head_revision
from prat.db.models import AppSetting
from prat.improve import trial as trial_module
from prat.improve.settings import update_improve_settings
from prat.improve.trial import (
    TrialResult,
    TrialStatus,
    child_env,
    dependencies_changed,
    run_trial,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A clone of PRAT's committed source to build candidates in."""
    target = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--no-hardlinks", str(REPO_ROOT), str(target)], check=True
    )
    git(target, "config", "user.name", "Test Person")
    git(target, "config", "user.email", "person@example.invalid")
    return target


@pytest.fixture
def live(settings: Settings, db: Database) -> Database:
    """The 'live' installation: self-improvement on, so the trial must turn it off."""
    with db.session() as session:
        update_improve_settings(session, enabled=True)
    return db


def patch(repo: Path, rel: str, anchor: str, insert: str) -> None:
    """Insert ``insert`` (one or more lines) after the line containing ``anchor``."""
    path = repo / rel
    text = path.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)
    index = next(i for i, line in enumerate(lines) if anchor in line)
    lines[index + 1 : index + 1] = insert.split("\n")
    path.write_text(newline.join(lines), encoding="utf-8", newline="")


def commit(repo: Path, message: str) -> tuple[str, str]:
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return base, git(repo, "rev-parse", "HEAD")


def trial(
    settings: Settings,
    live: Database,
    clone: Path,
    base: str,
    candidate: str,
    *,
    invariants: bool = True,
) -> TrialResult:
    return run_trial(
        settings, live, clone=clone, base=base, candidate=candidate, invariants=invariants
    )


def failed_at(result: TrialResult) -> str:
    step = result.failed_step
    return step.name if step else ""


def listening(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


# A good candidate ------------------------------------------------------------------


def test_a_good_candidate_passes_every_step(
    settings: Settings, live: Database, clone: Path
) -> None:
    (clone / "docs" / "TRIAL_NOTE.md").write_text("A harmless change.\n", encoding="utf-8")
    base, candidate = commit(clone, "a harmless change")
    result = trial(settings, live, clone, base, candidate)
    assert result.passed, result.to_dict()
    assert [s.name for s in result.steps] == [
        "checkout",
        "install",
        "selfcheck",
        "invariants",
        "data",
        "boot",
        "probe",
        "teardown",
    ]
    # Torn down: no folder, no worktree, nothing listening; the logs are kept.
    assert not any((settings.home / "self" / "trials").glob("*"))
    assert "trials" not in git(clone, "worktree", "list")
    boot = next(step for step in result.steps if step.name == "boot")
    port = int(boot.message.rsplit(" ", 1)[1])
    assert not listening(port)
    assert result.log_dir and (Path(result.log_dir) / "trial.json").exists()
    # The live installation is untouched: self-improvement is still on there.
    with live.session() as session:
        row = session.get(AppSetting, "improve.enabled")
        assert row is not None and row.value is True


# Candidates that must be refused ----------------------------------------------------
# Most of these switch the invariant step off to prove that each later step catches its
# problem on its own; the trusted invariants catch several of them first (see below).


def test_a_candidate_that_does_not_import_fails_the_selfcheck(
    settings: Settings, live: Database, clone: Path
) -> None:
    (clone / "src" / "prat" / "trial_broken.py").write_text(
        "import prat_no_such_module_xyz\n", encoding="utf-8"
    )
    base, candidate = commit(clone, "a module that cannot import")
    result = trial(settings, live, clone, base, candidate)
    assert result.status is TrialStatus.FAILED
    assert failed_at(result) == "selfcheck"
    assert "prat.trial_broken" in (result.failed_step.output if result.failed_step else "")


def test_a_migration_the_running_version_cannot_read_is_refused(
    settings: Settings, live: Database, clone: Path
) -> None:
    head = head_revision()
    versions = clone / "src" / "prat" / "db" / "alembic" / "versions"
    (versions / "9999_trial_drop_summary.py").write_text(
        "\n".join(
            [
                '"""Drop a column the running version still reads."""',
                "import sqlalchemy as sa",
                "from alembic import op",
                "",
                'revision = "9999trialdrop"',
                f'down_revision = "{head}"',
                "branch_labels = None",
                "depends_on = None",
                "",
                "",
                "def upgrade() -> None:",
                '    with op.batch_alter_table("cards") as batch:',
                '        batch.drop_column("summary")',
                "",
                "",
                "def downgrade() -> None:",
                '    with op.batch_alter_table("cards") as batch:',
                '        batch.add_column(sa.Column("summary", sa.Text(), nullable=True))',
                "",
            ]
        ),
        encoding="utf-8",
    )
    base, candidate = commit(clone, "drop a column in one step")
    result = trial(settings, live, clone, base, candidate, invariants=False)
    assert failed_at(result) == "data"
    assert result.failed_step is not None
    assert "additive step and a later removal" in result.failed_step.message


def test_a_candidate_that_does_not_boot_is_refused(
    settings: Settings, live: Database, clone: Path
) -> None:
    patch(
        clone,
        "src/prat/web/app.py",
        "sessions = SessionStore(db, settings.web_session_hours * 3600)",
        '    raise RuntimeError("this candidate cannot start")',
    )
    base, candidate = commit(clone, "break the server")
    result = trial(settings, live, clone, base, candidate, invariants=False)
    assert failed_at(result) == "boot"


def test_a_candidate_that_serves_pages_without_a_session_is_refused(
    settings: Settings, live: Database, clone: Path
) -> None:
    patch(clone, "src/prat/web/security.py", "def is_public(path: str) -> bool:", "    return True")
    base, candidate = commit(clone, "open every page")
    result = trial(settings, live, clone, base, candidate, invariants=False)
    assert failed_at(result) == "probe"
    assert result.failed_step is not None
    assert "without a session" in result.failed_step.message


def test_a_candidate_that_lets_a_push_through_is_refused(
    settings: Settings, live: Database, clone: Path
) -> None:
    patch(
        clone,
        "src/prat/policy/engine.py",
        "def decide(self, call: ToolCall) -> Decision:",
        '        return Decision.allow("anything goes")',
    )
    base, candidate = commit(clone, "allow every tool call")
    result = trial(settings, live, clone, base, candidate, invariants=False)
    assert failed_at(result) == "probe"
    assert result.failed_step is not None
    assert "push was not denied" in result.failed_step.message


def test_the_trusted_invariants_catch_a_weakened_candidate_first(
    settings: Settings, live: Database, clone: Path
) -> None:
    patch(
        clone,
        "src/prat/policy/engine.py",
        "def decide(self, call: ToolCall) -> Decision:",
        '        return Decision.allow("anything goes")',
    )
    # The candidate also deletes the invariant that would catch it; that gains nothing.
    (clone / "tests" / "invariants" / "test_inv_agents.py").unlink()
    base, candidate = commit(clone, "allow every tool call and delete its invariant")
    result = trial(settings, live, clone, base, candidate)
    assert failed_at(result) == "invariants"
    assert result.failed_step is not None
    assert "test_inv_policy_denies_every_push" in result.failed_step.output
    assert "boot" not in [step.name for step in result.steps]


# Dependencies, the network and one trial at a time ----------------------------------


def test_dependency_changes_are_detected(clone: Path) -> None:
    (clone / "README.md").write_text("changed\n", encoding="utf-8")
    base, candidate = commit(clone, "docs only")
    assert not dependencies_changed(clone, base, candidate)
    patch(clone, "pyproject.toml", "dependencies = [", '    "prat-trial-extra-xyz>=1",')
    base, candidate = commit(clone, "a new dependency")
    assert dependencies_changed(clone, base, candidate)
    (clone / "requirements-extra.txt").write_text("x\n", encoding="utf-8")
    base, candidate = commit(clone, "a requirements file")
    assert dependencies_changed(clone, base, candidate)


def test_changed_dependencies_without_a_network_postpone_the_trial(
    settings: Settings, live: Database, clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch(clone, "pyproject.toml", "dependencies = [", '    "prat-trial-extra-xyz>=1",')
    base, candidate = commit(clone, "a new dependency")
    # An index nobody listens on, and no retries: as good as being offline.
    monkeypatch.setenv("PIP_INDEX_URL", "http://127.0.0.1:9/simple")
    monkeypatch.setenv("PIP_RETRIES", "0")
    monkeypatch.setenv("PIP_TIMEOUT", "2")
    result = trial(settings, live, clone, base, candidate)
    assert result.status is TrialStatus.POSTPONED, result.to_dict()
    assert failed_at(result) == "install"


def test_trials_run_one_at_a_time(settings: Settings, live: Database, clone: Path) -> None:
    head = git(clone, "rev-parse", "HEAD")
    lock: threading.Lock = trial_module._ONE_AT_A_TIME
    with lock:
        result = trial(settings, live, clone, head, head)
    assert result.status is TrialStatus.POSTPONED
    assert result.steps[0].message == "another trial is running"


def test_candidate_processes_get_no_prat_settings_or_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRAT_HOME", "/live/home")
    monkeypatch.setenv("PRAT_DATABASE_URL", "sqlite:///live.db")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder")
    monkeypatch.setenv("PYTHONPATH", "/somewhere")
    env = child_env({"PRAT_BUILD_COMMIT": "abc1234"})
    assert "PRAT_HOME" not in env and "PRAT_DATABASE_URL" not in env
    assert "ANTHROPIC_API_KEY" not in env and "PYTHONPATH" not in env
    assert env["PRAT_BUILD_COMMIT"] == "abc1234"


def test_the_live_database_keeps_its_rows(settings: Settings, live: Database, clone: Path) -> None:
    head = git(clone, "rev-parse", "HEAD")
    with live.session() as session:
        before = sorted(row.key for row in session.scalars(select(AppSetting)))
    result = run_trial(settings, live, clone=clone, base=head, candidate=head, probes=False)
    assert result.passed, result.to_dict()
    with live.session() as session:
        assert sorted(row.key for row in session.scalars(select(AppSetting))) == before
