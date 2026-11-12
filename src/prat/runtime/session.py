"""Run one agent session to the end and collect what it said.

Used by the planner and the reviewer, which need the final answer rather
than a live stream. The budget is enforced here as well as by the runtime.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field

from prat.runtime.base import (
    AgentRuntime,
    AgentTask,
    BlockedEvent,
    CostEvent,
    DoneEvent,
    Outcome,
    RuntimeEvent,
    SessionStarted,
    TextEvent,
)


@dataclass
class SessionOutput:
    outcome: Outcome | None = None
    message: str = ""
    texts: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    session_id: str | None = None
    blocked_reason: str | None = None
    tools: tuple[str, ...] = ()

    @property
    def final_text(self) -> str:
        """The session's final answer: the result message, else the last text."""
        if self.message.strip():
            return self.message
        return self.texts[-1] if self.texts else ""


async def collect(
    runtime: AgentRuntime,
    task: AgentTask,
    *,
    on_event: Callable[[RuntimeEvent], None] | None = None,
) -> SessionOutput:
    out = SessionOutput()
    stream = runtime.run(task)
    try:
        async for event in stream:
            if on_event is not None:
                on_event(event)
            if isinstance(event, SessionStarted):
                out.session_id = event.session_id
                out.tools = event.tools
            elif isinstance(event, TextEvent):
                out.texts.append(event.text)
            elif isinstance(event, CostEvent):
                out.cost_usd += event.usd
                limit = task.budget.max_usd
                if limit is not None and out.cost_usd > limit:
                    out.outcome = Outcome.BUDGET_EXCEEDED
                    out.message = f"budget exceeded: ${out.cost_usd:.4f} of ${limit:.4f}"
                    if out.session_id:
                        await runtime.cancel(out.session_id)
                    break
            elif isinstance(event, BlockedEvent):
                out.blocked_reason = event.reason
            elif isinstance(event, DoneEvent):
                out.outcome = event.outcome
                out.message = event.message
                break
    finally:
        closer = getattr(stream, "aclose", None)
        if closer is not None:
            with contextlib.suppress(Exception):
                await closer()
    return out
