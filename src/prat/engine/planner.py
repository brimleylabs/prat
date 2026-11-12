"""The agent-backed planner.

For cards of the configured complexities (``high`` by default) a short plan
pass runs with read-only tools before the builder starts; its text is put
into the builder's prompt. A plan that ends with ``BLOCKED: <reason>``
blocks the card: the engine records that outcome and nothing later in the
run can overwrite it. A planner that fails for any other reason only means
the builder starts without a plan.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from prat.domain import Autonomy, Complexity
from prat.engine.instructions import load_repo_instructions
from prat.engine.interfaces import PlanRequest, PlanResult
from prat.engine.prompts import PLANNER_TASK, planner_system_prompt
from prat.policy import Policy
from prat.runtime.base import READ_TOOLS, AgentRuntime, AgentTask, Budget, Outcome
from prat.runtime.session import collect


class AgentPlanner:
    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        complexities: Iterable[Complexity | str] = (Complexity.HIGH,),
    ) -> None:
        self.runtime = runtime
        self.complexities = frozenset(str(c) for c in complexities)

    def task_for(self, request: PlanRequest) -> AgentTask:
        workdir = Path(request.workdir)
        return AgentTask(
            prompt=PLANNER_TASK,
            system_prompt=planner_system_prompt(
                title=request.title,
                goal=request.goal,
                acceptance_criteria=request.acceptance_criteria,
                spec=request.spec,
                repo_instructions=load_repo_instructions(workdir),
                knowledge=request.knowledge,
            ),
            workdir=workdir,
            allowed_tools=READ_TOOLS,
            policy=request.policy or Policy(Autonomy.READ_ONLY, workdir),
            budget=Budget(max_usd=request.budget_usd),
            model=request.model,
            metadata={"role": "planner", "card_id": str(request.card_id)},
        )

    async def plan(self, request: PlanRequest) -> PlanResult | None:
        if str(request.complexity) not in self.complexities:
            return None
        out = await collect(self.runtime, self.task_for(request), on_event=request.on_event)
        if out.outcome is Outcome.BLOCKED or out.blocked_reason:
            reason = out.blocked_reason or out.message or "the planner reported it is blocked"
            return PlanResult(blocked_reason=reason, cost_usd=out.cost_usd)
        if out.outcome is not Outcome.COMPLETED or not out.final_text.strip():
            what = f"{out.outcome or 'no result'}: {out.message}".strip()
            return PlanResult(
                error=f"planner did not produce a plan ({what})", cost_usd=out.cost_usd
            )
        return PlanResult(plan=out.final_text.strip(), cost_usd=out.cost_usd)
