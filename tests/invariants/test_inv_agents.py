"""Invariant: agent sessions cannot push, cannot write outside their worktree or into
protected or secret paths; read-only roles have no write tools; web tools belong to the
researcher alone."""

from __future__ import annotations

from pathlib import Path

import pytest
from invariant_support import git, make_repo, try_git

from prat.domain import Autonomy
from prat.git import WorktreeManager
from prat.policy import Policy, ToolCall
from prat.runtime import ALL_TOOLS, READ_TOOLS, FakeRuntime

PUSH_COMMANDS = [
    "git push",
    "git push origin main",
    "git push --force origin HEAD:main",
    "git push --no-verify",
    "git -C . push",
    "git -c core.hooksPath=/dev/null push",
    "env git push",
    "command git push",
    "sh -c 'git push'",
    'bash -c "git push origin"',
    "git push ../repo HEAD:main",
    "git send-pack ../repo main",
    "git remote set-url origin ../repo",
    "git config remote.origin.pushurl ../repo",
]


@pytest.mark.parametrize("level", list(Autonomy))
@pytest.mark.parametrize("command", PUSH_COMMANDS)
def test_inv_policy_denies_every_push_at_every_level(
    tmp_path: Path, level: Autonomy, command: str
) -> None:
    policy = Policy(level, tmp_path)
    assert not policy.decide(ToolCall.bash(command)).allowed, command


def test_inv_an_agent_worktree_cannot_push_for_real(tmp_path: Path) -> None:
    checkout = make_repo(tmp_path / "repos")
    manager = WorktreeManager(tmp_path / "worktrees")
    agent = tmp_path / "repos" / "inv-agent"
    manager.prepare_agent_clone(checkout, agent, "main")
    worktree = manager.create_worktree(agent, tmp_path / "worktrees" / "card-1", "card/1", "main")
    (worktree.path / "x.txt").write_text("x", encoding="utf-8")
    git(worktree.path, "add", "-A")
    git(worktree.path, "commit", "-q", "-m", "card work")
    before = git(checkout, "rev-parse", "main")
    attempts = [
        ("push",),
        ("push", "origin", "card/1"),
        ("push", "--no-verify", "origin", "HEAD:main"),
        ("push", str(checkout), "HEAD:main"),
        ("push", checkout.as_uri(), "HEAD:main"),
    ]
    for args in attempts:
        assert try_git(worktree.path, *args).returncode != 0, args
        assert try_git(agent, *args).returncode != 0, args
    assert git(checkout, "rev-parse", "main") == before
    assert WorktreeManager.push_blocked(agent)


def test_inv_writes_stay_inside_the_worktree_and_out_of_protected_paths(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    outside = tmp_path / "elsewhere"
    policy = Policy(
        Autonomy.HIGH,
        worktree,
        protected=["migrations/**"],
        denied_roots=[outside],
    )
    denied = [
        str(outside / "x.py"),
        str(tmp_path / "x.py"),
        str(worktree / ".git" / "config"),
        str(worktree / ".prat" / "project.toml"),
        str(worktree / "migrations" / "001.sql"),
        str(worktree / ".env"),
        str(worktree / "config" / "id_rsa"),
        "../escape.py",
    ]
    for path in denied:
        assert not policy.decide(ToolCall.write(path, "x")).allowed, path
    assert policy.decide(ToolCall.write(str(worktree / "app.py"), "x")).allowed
    for path in (str(worktree / ".env"), str(outside / "notes.txt")):
        assert not policy.decide(ToolCall.read(path)).allowed, path
    # Shell commands use forward slashes: in a POSIX shell a backslash is an escape.
    escape = (outside / "x").as_posix()
    for command in ("rm -rf ..", f"cp app.py {escape}", "echo x > ../x.txt"):
        assert not policy.decide(ToolCall.bash(command)).allowed, command


def test_inv_read_only_roles_have_no_write_tools(tmp_path: Path) -> None:
    from prat.engine.critic import AgentReviewer
    from prat.engine.interfaces import PlanRequest, ReviewRequest
    from prat.engine.planner import AgentPlanner

    runtime = FakeRuntime([])
    review = AgentReviewer(runtime).task_for(
        ReviewRequest(
            card_id=1,
            title="t",
            goal="g",
            acceptance_criteria="a",
            diff="+x",
            diff_stats={},
            gates=[],
            workdir=str(tmp_path),
        )
    )
    plan = AgentPlanner(runtime).task_for(
        PlanRequest(
            card_id=1,
            title="t",
            goal="g",
            acceptance_criteria="a",
            complexity="high",
            workdir=str(tmp_path),
        )
    )
    for task in (review, plan):
        assert task.allowed_tools <= READ_TOOLS, task.allowed_tools
        assert not task.policy(ToolCall.write(str(tmp_path / "x.py"), "x")).allowed
        assert not task.policy(ToolCall.bash("touch x.py")).allowed


def test_inv_web_tools_belong_to_the_researcher_alone(tmp_path: Path) -> None:
    from prat.improve.research import research_policy
    from prat.runtime import RESEARCH_TOOLS, WEB_TOOLS
    from prat.runtime.claude import judge, sdk_tool_names

    assert WEB_TOOLS
    assert not WEB_TOOLS & ALL_TOOLS
    assert RESEARCH_TOOLS == READ_TOOLS | WEB_TOOLS
    assert not {"WebSearch", "WebFetch"} & set(sdk_tool_names(ALL_TOOLS))
    search = ToolCall.web("web_search", {"query": "q"})
    for level in Autonomy:
        assert not Policy(level, tmp_path).decide(search).allowed
    researcher = research_policy(tmp_path)
    assert researcher.decide(search).allowed
    assert not judge(researcher, "WebSearch", {"query": "q"}, frozenset(ALL_TOOLS)).allowed
    assert not researcher.decide(ToolCall.write(str(tmp_path / "x.py"), "x")).allowed
