"""What PRAT keeps from the SDK's stream: models and cache, helpers, skills, task lists,
context fill, compaction, rate limit pressure and the session's own per-model usage."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from prat.runtime.base import (
    CompactEvent,
    ContextEvent,
    CostEvent,
    HelperEvent,
    PressureEvent,
    SkillEvent,
    TasksEvent,
    TextEvent,
    UsageEvent,
)
from prat.runtime.base import (
    CostEvent as Cost,
)
from prat.runtime.claude import MessageMapper
from prat.usage import UsageTally, cache_share, merge

MODEL = "claude-sonnet-5-5"
USAGE = {
    "input_tokens": 1000,
    "output_tokens": 200,
    "cache_read_input_tokens": 9000,
    "cache_creation_input_tokens": 500,
}


def say(*blocks: Any, parent: str | None = None, key: str = "m1") -> AssistantMessage:
    return AssistantMessage(
        content=list(blocks),
        model=MODEL,
        parent_tool_use_id=parent,
        usage=USAGE,
        message_id=key,
        session_id="s1",
    )


def of(events: list[Any], kind: type) -> list[Any]:
    return [e for e in events if isinstance(e, kind)]


def test_cost_keeps_the_model_and_the_cache_and_the_lead_reports_its_context() -> None:
    mapper = MessageMapper()
    events = mapper.map(say(TextBlock("hello")))
    (cost,) = of(events, CostEvent)
    assert cost.model == MODEL and cost.agent == "lead"
    assert (cost.cache_read_tokens, cost.cache_write_tokens) == (9000, 500)
    (context,) = of(events, ContextEvent)
    assert context.tokens == 10_700 and context.window == 200_000


def test_a_helper_is_followed_from_its_call_to_its_answer() -> None:
    mapper = MessageMapper()
    call = ToolUseBlock("t1", "Agent", {"subagent_type": "scout", "description": "find routes"})
    (started,) = of(mapper.map(say(call)), HelperEvent)
    assert (started.name, started.phase, started.prompt) == ("scout", "started", "find routes")
    # The helper's own work is spent by the helper, and its text is not the lead's.
    inner = mapper.map(say(TextBlock("looking"), parent="t1", key="m2"))
    assert not of(inner, TextEvent) and not of(inner, ContextEvent)
    (cost,) = of(inner, CostEvent)
    assert cost.agent == "scout"
    answer = UserMessage(content=[ToolResultBlock("t1", "routes are in api/routes.py")])
    (finished,) = of(mapper.map(answer), HelperEvent)
    assert finished.phase == "finished" and "api/routes.py" in finished.summary


def test_a_helper_in_the_background_finishes_with_its_notification() -> None:
    mapper = MessageMapper()
    mapper.map(say(ToolUseBlock("t1", "Agent", {"subagent_type": "tester"})))
    launched = UserMessage(content=[ToolResultBlock("t1", "status: async_launched")])
    assert not of(mapper.map(launched), HelperEvent)
    note = TaskNotificationMessage(
        "task_notification", {}, "k1", "completed", "", "2 tests fail: fixture", "u", "s1", "t1"
    )
    (finished,) = mapper.map(note)
    assert isinstance(finished, HelperEvent) and finished.summary.startswith("2 tests")


def test_skills_and_the_task_list_are_followed() -> None:
    mapper = MessageMapper()
    events = mapper.map(
        say(ToolUseBlock("k", "Skill", {"skill": "prat-loadout:safe-database-changes"}))
    )
    assert of(events, SkillEvent) == [SkillEvent("safe-database-changes", "lead")]
    todos = {
        "todos": [
            {"content": "schema", "status": "completed"},
            {"content": "api", "status": "in_progress"},
        ]
    }
    (tasks,) = of(mapper.map(say(ToolUseBlock("w", "TodoWrite", todos), key="m2")), TasksEvent)
    assert [(t.subject, t.status) for t in tasks.tasks] == [
        ("schema", "completed"),
        ("api", "in_progress"),
    ]


def test_task_create_and_update_build_the_same_list() -> None:
    mapper = MessageMapper()
    mapper.map(say(ToolUseBlock("c1", "TaskCreate", {"subject": "backfill"})))
    (created,) = of(
        mapper.map(UserMessage(content=[ToolResultBlock("c1", "Task #3 created")])), TasksEvent
    )
    assert created.tasks[0].subject == "backfill"
    update = ToolUseBlock("u1", "TaskUpdate", {"taskId": "3", "status": "completed"})
    (updated,) = of(mapper.map(say(update, key="m2")), TasksEvent)
    assert updated.tasks[0].status == "completed"


def test_compaction_pressure_and_the_final_usage() -> None:
    mapper = MessageMapper()
    (compact,) = mapper.map(
        SystemMessage(
            "compact_boundary", {"compact_metadata": {"pre_tokens": 180000, "trigger": "auto"}}
        )
    )
    assert compact == CompactEvent(180000, "auto")
    info = RateLimitInfo(
        "allowed_warning", resets_at=1800000000, rate_limit_type="five_hour", utilization=0.82
    )
    (pressure,) = mapper.map(RateLimitEvent(info, "u", "s1"))
    assert pressure == PressureEvent("allowed_warning", 0.82, 1800000000.0, "five_hour")
    result = ResultMessage(
        subtype="success",
        duration_ms=1000,
        duration_api_ms=900,
        is_error=False,
        num_turns=4,
        session_id="s1",
        result="done",
        model_usage={
            MODEL: {
                "inputTokens": 10,
                "outputTokens": 20,
                "cacheReadInputTokens": 90,
                "cacheCreationInputTokens": 5,
                "costUSD": 0.5,
            }
        },
    )
    (usage,) = of(mapper.map(result), UsageEvent)
    assert usage.models[MODEL]["cache_read"] == 90 and usage.turns == 4


def test_the_tally_trusts_the_sessions_own_totals() -> None:
    tally = UsageTally()
    tally.add(Cost(usd=0.1, input_tokens=5, model=MODEL, cache_read_tokens=50, agent="lead"))
    tally.add(Cost(usd=0.2, input_tokens=5, model="claude-haiku-5-5", agent="scout"))
    assert set(tally.to_dict()["models"]) == {MODEL, "claude-haiku-5-5"}
    tally.add(
        UsageEvent(
            {MODEL: {"input": 1, "output": 2, "cache_read": 3, "cache_write": 0, "usd": 1.0}}
        )
    )
    assert set(tally.to_dict()["models"]) == {MODEL}
    assert tally.to_dict()["agents"]["scout"] == 0.2
    total = merge([tally.to_dict(), tally.to_dict()])
    assert total[MODEL]["cache_read"] == 6
    assert cache_share(total[MODEL]) == 6 / 8
