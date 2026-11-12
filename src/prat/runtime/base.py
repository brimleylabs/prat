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

from collections.abc import AsyncIterator, Callable, Mapping
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
READ_TOOLS = frozenset({TOOL_READ, TOOL_GLOB, TOOL_GREP})
ALL_TOOLS = frozenset({*READ_TOOLS, TOOL_WRITE, TOOL_EDIT, TOOL_BASH, TOOL_MCP})
# Web tools are never part of ALL_TOOLS: only the researcher role is given them,
# together with read-only file tools.
WEB_TOOLS = frozenset({TOOL_WEB_SEARCH, TOOL_WEB_FETCH})
RESEARCH_TOOLS = READ_TOOLS | WEB_TOOLS


@dataclass(frozen=True)
class Budget:
    max_usd: float | None = None
    max_turns: int | None = None


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
    type: Literal["cost"] = "cost"


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
    | BlockedEvent
    | DoneEvent
)


def event_to_dict(event: RuntimeEvent) -> dict[str, Any]:
    data = dict(event.__dict__)
    if isinstance(event, DoneEvent):
        data["outcome"] = str(event.outcome)
    if "input" in data:
        data["input"] = dict(data["input"])
    if "tools" in data:
        data["tools"] = list(data["tools"])
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
