"""Invariant: the engine push is fast-forward only, pushes only the default branch, and
refuses any project that is not the self project; nothing lands without full proof."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from invariant_support import git, make_repo, setup

from prat.improve.guard import Evidence, judge
from prat.improve.landing import publish
from prat.improve.selfproject import self_repo_path, setup_self_project
from prat.improve.settings import update_improve_settings

HEAD = "a" * 40
FULL = Evidence(
    branch_head=HEAD,
    verified_commit=HEAD,
    gates_passed=True,
    verify_findings=(),
    criteria_verdict="met",
    criteria_commit=HEAD,
    security_verdict="pass",
    security_commit=HEAD,
    merge_commit="b" * 40,
    trial_status="passed",
)


def test_inv_the_push_is_fast_forward_only_and_for_the_self_project_alone(
    tmp_path: Path,
) -> None:
    s = setup(tmp_path)
    with pytest.raises(PermissionError):
        publish(s.db, s.settings, project_id=s.project.id)

    source = make_repo(tmp_path / "self-src", "prat")
    (source / "src" / "prat").mkdir(parents=True)
    (source / "src" / "prat" / "__init__.py").write_text("", encoding="utf-8")
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "package")
    bare = tmp_path / "remote.git"
    git(tmp_path, "clone", "-q", "--bare", str(source), str(bare))
    git(source, "remote", "add", "origin", str(bare))
    result = setup_self_project(s.db, s.settings, running_from=source / "src/prat/__init__.py")
    with s.db.session() as session:
        update_improve_settings(session, enabled=True)

    clone = self_repo_path(s.settings)
    (clone / "landed.txt").write_text("x", encoding="utf-8")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "landed")
    git(clone, "tag", "v-local")
    git(clone, "branch", "side")
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(bare), str(other))
    git(other, "config", "user.name", "T")
    git(other, "config", "user.email", "t@example.invalid")
    (other / "theirs.txt").write_text("y", encoding="utf-8")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "theirs")
    git(other, "push", "-q", "origin", "main")
    theirs = git(bare, "rev-parse", "main")

    refused = publish(s.db, s.settings, project_id=result.project_id)
    assert not refused.ok
    assert git(bare, "rev-parse", "main") == theirs  # never forced

    git(clone, "fetch", "-q", "origin")
    git(clone, "merge", "-q", "--no-edit", "origin/main")
    pushed = publish(s.db, s.settings, project_id=result.project_id)
    assert pushed.ok and pushed.pushed
    assert git(bare, "rev-parse", "main") == git(clone, "rev-parse", "main")
    assert git(bare, "tag") == ""  # no tags
    assert git(bare, "branch", "--list", "side") == ""  # no other branches


def test_inv_nothing_lands_without_full_proof() -> None:
    assert judge(FULL).lands
    missing = [
        {"gates_passed": False},
        {"verified_commit": None},
        {"verify_findings": ("secret found",)},
        {"criteria_verdict": "partial"},
        {"security_verdict": "concern"},
        {"security_verdict": None},
        {"trial_status": "failed"},
        {"trial_status": None},
        {"trial_status": "postponed"},
        {"criteria_commit": "c" * 40},
        {"security_commit": "c" * 40},
        {"remote_diverged": True},
    ]
    for changes in missing:
        assert not judge(replace(FULL, **changes)).lands, changes  # type: ignore[arg-type]
