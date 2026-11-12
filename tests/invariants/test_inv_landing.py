"""Invariant: nothing merges without passed verification, and approval is bound to the
commit the gates and the reviewer looked at."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from invariant_support import add_card, engine_for, git, run_card, setup

from prat import store
from prat.domain import CardStatus, Verdict
from prat.engine import ApproveError, FakeReviewer


def branch_of(s: object, card_id: int) -> str:
    with s.db.session() as session:  # type: ignore[attr-defined]
        branch = store.get_card(session, card_id).branch
        assert branch
        return branch


def test_inv_approval_is_bound_to_the_reviewed_commit(tmp_path: Path) -> None:
    s = setup(tmp_path)
    engine = engine_for(s)
    card_id = add_card(s)
    assert run_card(engine, card_id).card_status is CardStatus.REVIEW
    agent = Path(s.project.agent_path)
    branch = branch_of(s, card_id)
    tree = git(agent, "rev-parse", f"{branch}^{{tree}}")
    sneaked = git(agent, "commit-tree", tree, "-p", branch, "-m", "after review")
    git(agent, "update-ref", f"refs/heads/{branch}", sneaked)
    before = git(agent, "rev-parse", "main")
    for automatic in (False, True):
        with pytest.raises(ApproveError):
            asyncio.run(engine.approve(card_id, automatic=automatic))
    assert git(agent, "rev-parse", "main") == before


def test_inv_automatic_approval_needs_a_met_verdict(tmp_path: Path) -> None:
    s = setup(tmp_path)
    engine = engine_for(s, reviewer=FakeReviewer(Verdict.PARTIAL))
    card_id = add_card(s)
    assert run_card(engine, card_id).card_status is CardStatus.REVIEW
    with pytest.raises(ApproveError):
        asyncio.run(engine.approve(card_id, automatic=True))


def test_inv_a_failing_gate_never_reaches_review(tmp_path: Path) -> None:
    s = setup(tmp_path)
    toml = s.repo / ".prat" / "project.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace("sys.exit(0)", "sys.exit(1)"),
        encoding="utf-8",
        newline="\n",
    )
    git(s.repo, "commit", "-q", "-am", "failing gate")
    engine = engine_for(s)
    card_id = add_card(s)
    assert run_card(engine, card_id).card_status is CardStatus.FAILED
    with pytest.raises(ApproveError):
        asyncio.run(engine.approve(card_id))


def test_inv_a_required_security_review_must_pass(tmp_path: Path) -> None:
    from invariant_support import edit_project_file

    from prat.engine import FakeSecurityReviewer, SecurityVerdict

    s = setup(tmp_path)
    edit_project_file(
        s,
        "[verify]",
        '[review]\nreviewers = ["criteria", "security"]\n\n[verify]',
        "require the security review",
    )
    # A concern sends the card back, even though the criteria reviewer said met.
    concern = engine_for(s, security_reviewer=FakeSecurityReviewer(SecurityVerdict.CONCERN))
    first = add_card(s, "first")
    assert run_card(concern, first).card_status is CardStatus.REWORK
    # With no security reviewer at all, the review fails closed.
    missing = engine_for(s)
    second = add_card(s, "second")
    assert run_card(missing, second).card_status is CardStatus.REWORK
    with pytest.raises(ApproveError):
        asyncio.run(missing.approve(second, automatic=True))


def test_inv_an_empty_change_never_reaches_review(tmp_path: Path) -> None:
    s = setup(tmp_path)
    engine = engine_for(s, script=[{"op": "finish", "message": "all done, trust me"}])
    card_id = add_card(s)
    assert run_card(engine, card_id).card_status is CardStatus.FAILED
