"""The Claude Agent SDK adapter, driven by a fake SDK client (no network)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import CLIConnectionError, ToolPermissionContext

from prat.domain import Autonomy
from prat.engine.critic import AgentReviewer
from prat.engine.interfaces import ReviewRequest
from prat.policy import Decision, Policy, ToolCall
from prat.runtime import (
    ALL_TOOLS,
    READ_TOOLS,
    AgentTask,
    BlockedEvent,
    Budget,
    CostEvent,
    DoneEvent,
    Outcome,
    RuntimeEvent,
    SessionStarted,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from prat.runtime.claude import (
    DENIAL_PREFIX,
    ClaudeConfig,
    ClaudeRuntime,
    blocked_reason,
    judge,
    tool_calls_for,
)
from sdk_fakes import SESSION, FakeFactory, Hang, Tool, init, result, say


def make_task(
    workdir: Path,
    *,
    tools: frozenset[str] = frozenset(ALL_TOOLS - {"mcp"}),
    autonomy: Autonomy = Autonomy.MEDIUM,
    budget: float | None = 1.0,
    calls: list[ToolCall] | None = None,
) -> AgentTask:
    policy = Policy(autonomy, workdir)
    seen = calls if calls is not None else []

    def guarded(call: ToolCall) -> Decision:
        seen.append(call)
        return policy.decide(call)

    return AgentTask(
        prompt="do the card",
        workdir=workdir,
        allowed_tools=tools,
        policy=guarded,
        budget=Budget(max_usd=budget, max_turns=30),
        system_prompt="PRAT rules",
        model="claude-haiku-5-5",
    )


async def drain(runtime: ClaudeRuntime, task: AgentTask) -> list[RuntimeEvent]:
    return [event async for event in runtime.run(task)]


# Options ----------------------------------------------------------------------------


def test_options_restrict_tools_and_isolate_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in [k for k in os.environ if k.upper().startswith("PRAT_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("PRAT_DATABASE_URL", "mysql://PRAT:secret@127.0.0.1/PRAT")
    runtime = ClaudeRuntime(ClaudeConfig(env={"ANTHROPIC_API_KEY": "placeholder"}))
    options = runtime.build_options(make_task(tmp_path), resume="old-session")
    assert sorted(options.tools or []) == ["Bash", "Edit", "Glob", "Grep", "Read", "Write"]
    assert options.allowed_tools == []  # never used: it pre-approves rather than restricts
    for name in ("WebFetch", "WebSearch", "Agent", "Task", "Skill", "NotebookEdit"):
        assert name in options.disallowed_tools
    assert "Write" not in options.disallowed_tools
    assert options.setting_sources == []
    assert options.strict_mcp_config is True
    assert options.permission_mode == "default"
    assert options.max_budget_usd == 1.0
    assert options.max_turns == 30
    assert options.model == "claude-haiku-5-5"
    assert options.resume == "old-session"
    assert options.cwd == str(tmp_path)
    # PRAT's own settings are blanked so the agent's commands cannot read them.
    assert options.env == {"ANTHROPIC_API_KEY": "placeholder", "PRAT_DATABASE_URL": ""}
    assert options.can_use_tool is not None
    assert options.hooks is not None
    matcher = options.hooks["PreToolUse"][0]
    assert matcher.matcher is None  # every tool, MCP included
    assert matcher.on_failure == "block"  # a crashing hook denies, it never allows
    assert isinstance(options.system_prompt, dict)
    assert options.system_prompt.get("append") == "PRAT rules"


def test_critic_session_is_actually_read_only(tmp_path: Path) -> None:
    reviewer = AgentReviewer(ClaudeRuntime(client_factory=FakeFactory([])))
    task = reviewer.task_for(
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
    assert task.allowed_tools == READ_TOOLS
    options = ClaudeRuntime().build_options(task)
    assert sorted(options.tools or []) == ["Glob", "Grep", "Read"]
    for name in ("Write", "Edit", "Bash", "NotebookEdit", "WebFetch", "Agent"):
        assert name in options.disallowed_tools
        assert name not in (options.tools or [])


async def test_critic_hook_denies_write_and_bash_even_if_asked(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    factory = FakeFactory(
        [
            init(["Read", "Glob", "Grep"]),
            Tool("Read", {"file_path": "a.py"}, id="r"),
            Tool("Write", {"file_path": "a.py", "content": "x = 2\n"}, id="w"),
            Tool("Bash", {"command": "echo hi"}, id="b"),
            result('```json\n{"verdict": "met", "summary": "fine"}\n```'),
        ]
    )
    reviewer = AgentReviewer(ClaudeRuntime(client_factory=factory))
    review = await reviewer.review(
        ReviewRequest(
            card_id=1,
            title="t",
            goal="g",
            acceptance_criteria="a",
            diff="+x = 1",
            diff_stats={},
            gates=[],
            workdir=str(tmp_path),
            policy=Policy(Autonomy.READ_ONLY, tmp_path),
        )
    )
    decisions = [
        o["hookSpecificOutput"]["permissionDecision"] for o in factory.clients[0].hook_outputs
    ]
    assert decisions == ["allow", "deny", "deny"]
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 1\n"
    assert str(review.verdict) == "met"


# Hook decisions ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "tool_input", "allowed"),
    [
        ("Read", {"file_path": "src/app.py"}, True),
        ("Read", {"file_path": ".env"}, False),
        ("Write", {"file_path": "new.py", "content": "print(1)\n"}, True),
        ("Write", {"file_path": ".prat/project.toml", "content": "x"}, False),
        ("Write", {"file_path": "../outside.txt", "content": "x"}, False),
        ("Edit", {"file_path": "a.py", "old_string": "a", "new_string": "b"}, True),
        ("Bash", {"command": "git status"}, True),
        ("Bash", {"command": "git push origin HEAD"}, False),
        ("Bash", {"command": "sleep 100", "run_in_background": True}, False),
        ("Glob", {"pattern": "**/*.py"}, True),
        ("Grep", {"pattern": "def ", "path": "src"}, True),
        ("WebFetch", {"url": "https://example.invalid"}, False),
        ("Agent", {"prompt": "do it"}, False),
        ("mcp__tools__run", {"command": "ls"}, False),  # unregistered MCP tool
    ],
)
def test_judge_maps_every_tool_to_the_policy(
    tmp_path: Path, tool: str, tool_input: dict[str, Any], allowed: bool
) -> None:
    policy = Policy(Autonomy.MEDIUM, tmp_path)
    assert judge(policy, tool, tool_input).allowed is allowed


def test_glob_patterns_that_leave_the_root_are_checked(tmp_path: Path) -> None:
    calls = tool_calls_for("Glob", {"pattern": "../../secrets/*.pem"})
    assert isinstance(calls, list)
    assert [c.path for c in calls] == [".", "../../secrets"]
    calls = tool_calls_for("Grep", {"pattern": "key", "glob": "/etc/**"})
    assert isinstance(calls, list) and calls[-1].path == "/etc"


def test_judge_denies_tools_the_task_did_not_enable(tmp_path: Path) -> None:
    policy = Policy(Autonomy.MEDIUM, tmp_path)
    assert judge(policy, "Read", {"file_path": "a"}, READ_TOOLS).allowed
    assert not judge(policy, "Bash", {"command": "ls"}, READ_TOOLS).allowed
    assert not judge(policy, "Write", {"file_path": "a"}, READ_TOOLS).allowed
    assert "MCP" in judge(policy, "mcp__x__y", {}, READ_TOOLS).reason


def test_judge_fails_closed_when_the_policy_raises(tmp_path: Path) -> None:
    def broken(call: ToolCall) -> Decision:
        raise RuntimeError("boom")

    decision = judge(broken, "Read", {"file_path": "a"})
    assert not decision.allowed and "boom" in decision.reason


async def test_can_use_tool_fallback_never_approves(tmp_path: Path) -> None:
    options = ClaudeRuntime().build_options(make_task(tmp_path))
    assert options.can_use_tool is not None
    answer = await options.can_use_tool("Write", {}, ToolPermissionContext())
    assert answer.behavior == "deny"


# Message mapping -------------------------------------------------------------------------


async def test_messages_map_to_prat_events(tmp_path: Path) -> None:
    calls: list[ToolCall] = []
    factory = FakeFactory(
        [
            init(["Read", "Write", "Bash"]),
            say("Working on it", usage={"input_tokens": 1000, "output_tokens": 200}),
            Tool("Write", {"file_path": "hello.py", "content": "def hi():\n    pass\n"}, id="w1"),
            Tool("Bash", {"command": "git push"}, id="b1"),
            result("All done", total=0.01),
        ]
    )
    runtime = ClaudeRuntime(client_factory=factory)
    events = await drain(runtime, make_task(tmp_path, calls=calls))

    started = events[0]
    assert isinstance(started, SessionStarted) and started.session_id == SESSION
    assert started.tools == ("Read", "Write", "Bash") and not started.resumed
    assert any(isinstance(e, TextEvent) and e.text == "Working on it" for e in events)
    tool_calls = [e for e in events if isinstance(e, ToolCallEvent)]
    assert [(c.call_id, c.tool) for c in tool_calls] == [("w1", "Write"), ("b1", "Bash")]
    results = {e.call_id: e for e in events if isinstance(e, ToolResultEvent)}
    assert results["w1"].ok and not results["w1"].denied
    assert results["b1"].denied and "git push is not allowed" in results["b1"].reason
    # The denial reached the model as the hook's reason.
    denial = factory.clients[0].hook_outputs[1]["hookSpecificOutput"]
    assert denial["permissionDecisionReason"].startswith(DENIAL_PREFIX)
    assert (tmp_path / "hello.py").exists()
    assert [c.kind for c in calls] == ["file_write", "bash"]

    costs = [e for e in events if isinstance(e, CostEvent)]
    assert all(c.estimated for c in costs)
    assert costs[0].usd == pytest.approx((1000 * 0.10 + 200 * 0.50) / 1_000_000)
    assert sum(c.usd for c in costs) == pytest.approx(0.01)  # reconciled with the total
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.outcome is Outcome.COMPLETED
    assert done.message == "All done"
    client = factory.clients[0]
    assert client.prompts == ["do the card"] and client.disconnected


async def test_blocked_protocol(tmp_path: Path) -> None:
    factory = FakeFactory(
        [init(), result("I looked around.\n\nBLOCKED: which database should this use?")]
    )
    events = await drain(ClaudeRuntime(client_factory=factory), make_task(tmp_path))
    blocked = [e for e in events if isinstance(e, BlockedEvent)]
    assert blocked and blocked[0].reason == "which database should this use?"
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.outcome is Outcome.BLOCKED


def test_blocked_reason_takes_the_last_line() -> None:
    assert blocked_reason("text\nBLOCKED: first\nmore\n**BLOCKED:** second  ") == "second"
    assert blocked_reason("Nothing is blocked here") is None
    assert blocked_reason(None) is None


@pytest.mark.parametrize(
    ("message", "outcome"),
    [
        (result("over", subtype="error_max_budget_usd", is_error=True), Outcome.BUDGET_EXCEEDED),
        (result("turns", subtype="error_max_turns", is_error=True), Outcome.BUDGET_EXCEEDED),
        (
            result("", subtype="error_during_execution", is_error=True, errors=["api down"]),
            Outcome.ERROR,
        ),
        (result("Invalid API key", is_error=True), Outcome.ERROR),
    ],
)
async def test_result_subtypes(tmp_path: Path, message: Any, outcome: Outcome) -> None:
    events = await drain(
        ClaudeRuntime(client_factory=FakeFactory([init(), message])), make_task(tmp_path)
    )
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.outcome is outcome


async def test_connect_failure_is_an_error_event(tmp_path: Path) -> None:
    factory = FakeFactory([], fail_connect=CLIConnectionError("no binary"))
    events = await drain(ClaudeRuntime(client_factory=factory), make_task(tmp_path))
    assert len(events) == 1
    done = events[0]
    assert isinstance(done, DoneEvent) and done.outcome is Outcome.ERROR
    assert "no binary" in done.message
    assert factory.clients[0].disconnected


async def test_stream_ending_without_result_is_an_error(tmp_path: Path) -> None:
    events = await drain(ClaudeRuntime(client_factory=FakeFactory([init()])), make_task(tmp_path))
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.outcome is Outcome.ERROR


async def test_resume_uses_the_session_id(tmp_path: Path) -> None:
    factory = FakeFactory([init(), result()])
    runtime = ClaudeRuntime(client_factory=factory)
    events = [e async for e in runtime.resume("old-session", make_task(tmp_path))]
    assert factory.clients[0].options.resume == "old-session"
    assert "Continue the same task" in factory.clients[0].prompts[0]
    started = events[0]
    assert isinstance(started, SessionStarted) and started.resumed


async def test_cancel_stops_the_session_and_disconnects(tmp_path: Path) -> None:
    factory = FakeFactory([init(), say("thinking"), Hang()])
    runtime = ClaudeRuntime(client_factory=factory)
    stream = runtime.run(make_task(tmp_path))
    first = await anext(stream)
    assert isinstance(first, SessionStarted)
    assert isinstance(await anext(stream), TextEvent)
    pending = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0.05)
    assert not pending.done()
    await runtime.cancel(first.session_id)
    done = await asyncio.wait_for(pending, 5)
    assert isinstance(done, DoneEvent) and done.outcome is Outcome.CANCELLED
    await stream.aclose()  # type: ignore[attr-defined]
    client = factory.clients[0]
    assert client.interrupted and client.disconnected
    await runtime.cancel(first.session_id)  # idempotent


async def test_cancelling_the_consumer_task_disconnects(tmp_path: Path) -> None:
    factory = FakeFactory([init(), Hang()])
    runtime = ClaudeRuntime(client_factory=factory)

    async def consume() -> None:
        async for _ in runtime.run(make_task(tmp_path)):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert factory.clients[0].disconnected
