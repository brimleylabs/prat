"""The agent runtime interface.

An :class:`AgentRuntime` runs one agent session for one task and yields typed
events. Every tool call the agent wants to make must be passed to
``task.policy`` first; a denied call is reported back to the agent and never
executed. The engine journals every event and enforces the budget itself from
:class:`CostEvent` values, so a runtime that under-reports its limits still
gets stopped.

Adapters: :mod:`prat.runtime.fake` (scripted) and :mod:`prat.runtime.claude`
(the Claude Agent SDK, with an API key or the person's own Claude Code
login). Agent Client Protocol agents are planned.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from prat.policy import Decision, ToolCall

PolicyCallback = Callable[[ToolCall], Decision]

# Tool names a runtime may expose. Adapters map these onto their own tools.
TOOL_READ = "read"
TOOL_WRITE = "write"
TOOL_EDIT = "edit"
TOOL_BASH = "bash"
TOOL_GLOB = "glob"
TOOL_GREP = "grep"
TOOL_MCP = "mcp"
TOOL_WEB_SEARCH = "web_search"
TOOL_WEB_FETCH = "web_fetch"
# The agent's own task list, its helpers (subagents) and its skills. None of them acts on
# anything by itself: a helper's own tool calls go through the policy like the lead's.
TOOL_TASKS = "tasks"
TOOL_HELPERS = "helpers"
TOOL_SKILLS = "skills"
READ_TOOLS = frozenset({TOOL_READ, TOOL_GLOB, TOOL_GREP})
ALL_TOOLS = frozenset({*READ_TOOLS, TOOL_WRITE, TOOL_EDIT, TOOL_BASH, TOOL_MCP})
CREW_TOOLS = frozenset({TOOL_TASKS, TOOL_HELPERS, TOOL_SKILLS})
# Web tools are never part of ALL_TOOLS: only the researcher role is given them,
# together with read-only file tools.
WEB_TOOLS = frozenset({TOOL_WEB_SEARCH, TOOL_WEB_FETCH})
RESEARCH_TOOLS = READ_TOOLS | WEB_TOOLS


@dataclass(frozen=True)
class Budget:
    max_usd: float | None = None
    max_turns: int | None = None


@dataclass(frozen=True)
class HelperSpec:
    """A helper the agent may call (a subagent): what it is for, its instructions, its PRAT
    tool set (a subset of the task's) and its model (``None`` = the lead's)."""

    description: str
    prompt: str
    tools: frozenset[str]
    model: str | None = None


ToolHandler = Callable[[Mapping[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class PratTool:
    """One of PRAT's own tools offered to the agent (report progress, propose a skill, ...).
    ``schema`` maps each argument to its type (str, int, float or bool); the handler returns
    the text the agent reads back."""

    name: str
    description: str
    schema: Mapping[str, type]
    handler: ToolHandler


@dataclass(frozen=True)
class AgentTask:
    prompt: str
    workdir: Path
    allowed_tools: frozenset[str]
    policy: PolicyCallback
    budget: Budget = field(default_factory=Budget)
    system_prompt: str = ""
    model: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    # Extra environment for the agent's own tools (its shell commands), for example
    # the project's database pointed at the card's branch.
    env: Mapping[str, str] = field(default_factory=dict)
    # With the sandbox, turns an approved shell command into the one that runs it in a
    # container (prat.sandbox). ``None`` runs shell commands on this machine.
    shell: Callable[[str], str] | None = None
    # The crew: helpers the agent may call (with TOOL_HELPERS), the folders its skills are
    # loaded from (with TOOL_SKILLS), and PRAT's own tools.
    helpers: Mapping[str, HelperSpec] = field(default_factory=dict)
    skill_dirs: tuple[Path, ...] = ()
    prat_tools: tuple[PratTool, ...] = ()
    # Let the model think before it acts; its thoughts come back as ThoughtEvents.
    thinking: bool = False


class Outcome(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    BUDGET_EXCEEDED = "budget_exceeded"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class SessionStarted:
    session_id: str
    resumed: bool = False
    # What the runtime actually made available, as reported by the agent itself.
    tools: tuple[str, ...] = ()
    model: str = ""
    type: Literal["session"] = "session"


@dataclass(frozen=True)
class TextEvent:
    text: str
    type: Literal["text"] = "text"


@dataclass(frozen=True)
class ToolCallEvent:
    call_id: str
    tool: str
    input: Mapping[str, Any]
    type: Literal["tool_call"] = "tool_call"


@dataclass(frozen=True)
class ToolResultEvent:
    call_id: str
    ok: bool
    output: str = ""
    denied: bool = False
    reason: str = ""
    type: Literal["tool_result"] = "tool_result"


@dataclass(frozen=True)
class CostEvent:
    usd: float
    input_tokens: int = 0
    output_tokens: int = 0
    # Costs reported by runtimes are estimates unless billing says otherwise.
    estimated: bool = True
    # The model that answered, prompt cache use, and which agent spent it: "lead" for the
    # session's own agent, or the name of the helper (subagent) that did.
    model: str = ""
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    agent: str = "lead"
    type: Literal["cost"] = "cost"


@dataclass(frozen=True)
class UsageEvent:
    """The session's own tally per model at its end (helpers included), which replaces the
    running estimate: ``{model: {input, output, cache_read, cache_write, usd}}``."""

    models: Mapping[str, Mapping[str, float]]
    turns: int = 0
    duration_ms: int = 0
    type: Literal["usage"] = "usage"


@dataclass(frozen=True)
class TaskItem:
    subject: str
    status: str  # pending, in_progress, completed


@dataclass(frozen=True)
class TasksEvent:
    """The agent's own task list, whole, each time it changes."""

    tasks: tuple[TaskItem, ...]
    type: Literal["tasks"] = "tasks"


@dataclass(frozen=True)
class HelperEvent:
    """A helper (subagent) the agent called: started, then finished with its answer."""

    call_id: str
    name: str
    phase: Literal["started", "finished"]
    prompt: str = ""
    summary: str = ""
    ok: bool = True
    type: Literal["helper"] = "helper"


@dataclass(frozen=True)
class SkillEvent:
    """A skill the agent (or one of its helpers) loaded."""

    name: str
    agent: str = "lead"
    type: Literal["skill"] = "skill"


@dataclass(frozen=True)
class ContextEvent:
    """How full the lead's context window is, after each of its responses."""

    tokens: int
    window: int
    type: Literal["context"] = "context"


@dataclass(frozen=True)
class CompactEvent:
    """The session compacted its context to keep going."""

    pre_tokens: int = 0
    trigger: str = ""
    type: Literal["compact"] = "compact"


@dataclass(frozen=True)
class ThoughtEvent:
    """What the lead thought before it acted (a summary of its reasoning)."""

    text: str
    type: Literal["thought"] = "thought"


@dataclass(frozen=True)
class PressureEvent:
    """Rate limit pressure reported by the service: status allowed, allowed_warning or
    rejected, how much of the limit is used, and when it resets."""

    status: str
    utilization: float | None = None
    resets_at: float | None = None
    kind: str = ""
    type: Literal["pressure"] = "pressure"


@dataclass(frozen=True)
class BlockedEvent:
    reason: str
    type: Literal["blocked"] = "blocked"


@dataclass(frozen=True)
class DoneEvent:
    outcome: Outcome
    message: str = ""
    type: Literal["done"] = "done"


RuntimeEvent = (
    SessionStarted
    | TextEvent
    | ToolCallEvent
    | ToolResultEvent
    | CostEvent
    | UsageEvent
    | TasksEvent
    | HelperEvent
    | SkillEvent
    | ContextEvent
    | CompactEvent
    | ThoughtEvent
    | PressureEvent
    | BlockedEvent
    | DoneEvent
)


def event_to_dict(event: RuntimeEvent) -> dict[str, Any]:
    data = dict(event.__dict__)
    if isinstance(event, DoneEvent):
        data["outcome"] = str(event.outcome)
    if "input" in data:
        data["input"] = dict(data["input"])
    if isinstance(event, SessionStarted):
        data["tools"] = list(data["tools"])
    if isinstance(event, TasksEvent):
        data["tasks"] = [{"subject": t.subject, "status": t.status} for t in event.tasks]
    if isinstance(event, UsageEvent):
        data["models"] = {k: dict(v) for k, v in event.models.items()}
    return data


@runtime_checkable
class AgentRuntime(Protocol):
    """Runs agent sessions. Implementations must be safe to share across runs."""

    name: str

    def run(self, task: AgentTask) -> AsyncIterator[RuntimeEvent]:
        """Start a new session. The first event should be :class:`SessionStarted`."""
        ...

    def resume(self, session_id: str, task: AgentTask) -> AsyncIterator[RuntimeEvent]:
        """Continue an earlier session after an interruption."""
        ...

    async def cancel(self, session_id: str) -> None:
        """Stop a running session as soon as possible. Must be idempotent."""
        ...
