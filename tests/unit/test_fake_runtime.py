from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from prat.policy import Decision, ToolCall
from prat.runtime import (
    AgentRuntime,
    AgentTask,
    BlockedEvent,
    Budget,
    CostEvent,
    DoneEvent,
    FakeCrash,
    FakeRuntime,
    FakeScript,
    Outcome,
    RuntimeEvent,
    SessionStarted,
    ToolResultEvent,
)


def allow_all(call: ToolCall) -> Decision:
    return Decision.allow()


def deny_writes(call: ToolCall) -> Decision:
    if call.kind == "file_write":
        return Decision.deny("no writes")
    return Decision.allow()


def task(tmp_path: Path, policy: Any = allow_all, **kwargs: Any) -> AgentTask:
    return AgentTask(
        prompt="do it",
        workdir=tmp_path,
        allowed_tools=frozenset({"write"}),
        policy=policy,
        **kwargs,
    )


async def collect(stream: Any) -> list[RuntimeEvent]:
    return [event async for event in stream]


def test_fake_runtime_satisfies_protocol() -> None:
    assert isinstance(FakeRuntime([]), AgentRuntime)


async def test_runs_script_and_writes_files(tmp_path: Path) -> None:
    runtime = FakeRuntime(
        [
            {"op": "text", "text": "hi"},
            {"op": "write", "path": "a/b.txt", "content": "x\n"},
            {"op": "edit", "path": "a/b.txt", "old": "x", "new": "y"},
            {"op": "read", "path": "a/b.txt"},
            {"op": "cost", "usd": 0.5},
            {"op": "finish", "message": "done"},
        ]
    )
    events = await collect(runtime.run(task(tmp_path)))
    assert isinstance(events[0], SessionStarted)
    assert (tmp_path / "a" / "b.txt").read_text() == "y\n"
    assert any(isinstance(e, CostEvent) and e.usd == 0.5 for e in events)
    assert events[-1] == DoneEvent(Outcome.COMPLETED, "done")


async def test_policy_denial_is_reported_and_not_executed(tmp_path: Path) -> None:
    runtime = FakeRuntime([{"op": "write", "path": "a.txt", "content": "x"}])
    events = await collect(runtime.run(task(tmp_path, deny_writes)))
    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert results[0].denied
    assert results[0].reason == "no writes"
    assert not (tmp_path / "a.txt").exists()


async def test_bash_runs_simple_commands(tmp_path: Path) -> None:
    runtime = FakeRuntime([{"op": "bash", "command": "{python} -c \"print('ok')\""}])
    events = await collect(runtime.run(task(tmp_path)))
    [result] = [e for e in events if isinstance(e, ToolResultEvent)]
    assert result.ok
    assert "ok" in result.output


async def test_block_and_error(tmp_path: Path) -> None:
    events = await collect(
        FakeRuntime([{"op": "block", "reason": "which db?"}]).run(task(tmp_path))
    )
    assert BlockedEvent("which db?") in events
    assert events[-1].outcome is Outcome.BLOCKED  # type: ignore[union-attr]
    events = await collect(FakeRuntime([{"op": "error", "message": "boom"}]).run(task(tmp_path)))
    assert events[-1] == DoneEvent(Outcome.ERROR, "boom")


async def test_exceed_budget_reports_cost_over_limit(tmp_path: Path) -> None:
    runtime = FakeRuntime([{"op": "exceed_budget"}])
    events = await collect(runtime.run(task(tmp_path, budget=Budget(max_usd=2.0))))
    costs = [e for e in events if isinstance(e, CostEvent)]
    assert costs[0].usd > 2.0


async def test_turn_limit(tmp_path: Path) -> None:
    runtime = FakeRuntime([{"op": "read", "path": "x"}] * 3)
    events = await collect(runtime.run(task(tmp_path, budget=Budget(max_turns=2))))
    assert events[-1] == DoneEvent(Outcome.BUDGET_EXCEEDED, "turn limit reached")


async def test_crash_then_resume_continues_after_crash(tmp_path: Path) -> None:
    script = FakeScript.parse(
        {
            "session_id": "s-1",
            "steps": [
                {"op": "write", "path": "one.txt", "content": "1"},
                {"op": "crash"},
                {"op": "write", "path": "two.txt", "content": "2"},
            ],
        }
    )
    first = FakeRuntime(script, state_dir=tmp_path / "state")
    with pytest.raises(FakeCrash):
        await collect(first.run(task(tmp_path)))
    assert not (tmp_path / "two.txt").exists()
    second = FakeRuntime(script, state_dir=tmp_path / "state")
    events = await collect(second.resume("s-1", task(tmp_path)))
    assert isinstance(events[0], SessionStarted) and events[0].resumed
    assert (tmp_path / "two.txt").read_text() == "2"
    assert second.executed == [("write", "two.txt")]


async def test_cancel_stops_a_wait(tmp_path: Path) -> None:
    runtime = FakeRuntime(
        FakeScript.parse({"session_id": "s-2", "steps": [{"op": "wait", "seconds": 30}]})
    )

    async def cancel_soon() -> None:
        await asyncio.sleep(0.1)
        await runtime.cancel("s-2")

    canceller = asyncio.create_task(cancel_soon())
    events = await asyncio.wait_for(collect(runtime.run(task(tmp_path))), timeout=5)
    await canceller
    assert events[-1].outcome is Outcome.CANCELLED  # type: ignore[union-attr]
    assert "s-2" in runtime.cancelled


def test_script_validation(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        FakeScript.parse([{"op": "launch_missiles"}])
    with pytest.raises(ValidationError):
        FakeScript.parse([{"op": "write", "unknown": 1}])
    file = tmp_path / "s.json"
    file.write_text('[{"op": "finish"}]', encoding="utf-8")
    assert FakeScript.load(file).steps[0].op == "finish"


async def test_a_dry_step_asks_the_policy_and_does_nothing(tmp_path: Path) -> None:
    asked: list[ToolCall] = []

    def record(call: ToolCall) -> Decision:
        asked.append(call)
        return Decision.allow()

    runtime = FakeRuntime(
        [
            {"op": "bash", "command": "touch made.txt", "dry": True},
            {"op": "write", "path": "dry.txt", "content": "x", "dry": True},
        ]
    )
    events = await collect(runtime.run(task(tmp_path, record)))
    assert [call.kind for call in asked] == ["bash", "file_write"]
    assert not (tmp_path / "made.txt").exists()
    assert not (tmp_path / "dry.txt").exists()
    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert all(result.ok and "not carried out" in result.output for result in results)
    assert runtime.executed == []


async def test_delete_removes_a_file_and_is_a_write(tmp_path: Path) -> None:
    (tmp_path / "old.txt").write_text("x", encoding="utf-8")
    runtime = FakeRuntime([{"op": "delete", "path": "old.txt"}])
    await collect(runtime.run(task(tmp_path)))
    assert not (tmp_path / "old.txt").exists()

    (tmp_path / "kept.txt").write_text("x", encoding="utf-8")
    denied = FakeRuntime([{"op": "delete", "path": "kept.txt"}])
    events = await collect(denied.run(task(tmp_path, deny_writes)))
    assert (tmp_path / "kept.txt").exists()
    assert any(isinstance(e, ToolResultEvent) and e.denied for e in events)
