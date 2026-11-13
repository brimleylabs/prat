"""Plain Claude Code, the baseline the review layer is measured against.

One Claude Code session works on the case's card in the case's repository: Claude Code's
own system prompt, every permission, the same model as PRAT's builder and the same budget
per run. There is no PRAT policy, no gates, no reviewer and no rework: whatever the session
leaves behind is handed over. It gets what PRAT's builder gets from the card: the title,
the goal, the acceptance criteria and the project's notes.

It runs on this machine even when PRAT's sandbox is on, so run it on cases you trust.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prat.config import Settings
from prat.evals.cases import EvalCase
from prat.journal import Journal
from prat.model_registry import Role, resolve_model
from prat.projects import ProjectSpec

SESSION_TIMEOUT_SECONDS = 3600.0


@dataclass(frozen=True)
class PlainSession:
    status: str
    reason: str
    cost_usd: float
    turns: int


def plain_prompt(case: EvalCase, spec: ProjectSpec) -> str:
    parts = [f"# {case.spec.title}", case.spec.goal.strip()]
    if case.spec.acceptance_criteria.strip():
        parts += ["## Acceptance criteria", case.spec.acceptance_criteria.strip()]
    if spec.notes.strip():
        parts += ["## About this project", spec.notes.strip()]
    return "\n\n".join(part for part in parts if part) + "\n"


async def run_plain(
    settings: Settings,
    case: EvalCase,
    spec: ProjectSpec,
    workdir: Path,
    *,
    journal: Path | None = None,
    time_limit: float = SESSION_TIMEOUT_SECONDS,
) -> PlainSession:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    from prat.runtime.auth import runtime_env

    model = resolve_model(Role.BUILDER, settings_models=settings.models, project_models=spec.models)
    options = ClaudeAgentOptions(
        system_prompt={"type": "preset", "preset": "claude_code"},
        permission_mode="bypassPermissions",
        # The repository's own CLAUDE.md and settings, as in a developer's session.
        setting_sources=["project"],
        cwd=str(workdir),
        model=model,
        max_budget_usd=settings.default_budget_usd,
        env={
            **{key: "" for key in os.environ if key.upper().startswith("PRAT_")},
            **runtime_env(settings),
        },
        cli_path=settings.claude_cli_path,
    )
    log: Journal | None = None
    if journal is not None:
        journal.parent.mkdir(parents=True, exist_ok=True)
        log = Journal(journal, payload_limit=settings.journal_payload_limit)
        log.append("plain.started", {"model": model, "prompt": plain_prompt(case, spec)})
    outcome = {"status": "failed", "reason": "the session ended without a result"}
    totals = {"cost": 0.0, "turns": 0}

    async def drive() -> None:
        async for message in query(prompt=plain_prompt(case, spec), options=options):
            if log is not None:
                log.append(f"plain.{type(message).__name__}", _as_data(message))
            if isinstance(message, ResultMessage):
                totals["cost"] = float(message.total_cost_usd or 0.0)
                totals["turns"] = int(message.num_turns or 0)
                outcome["status"] = "failed" if message.is_error else "succeeded"
                outcome["reason"] = str(message.subtype)

    try:
        await asyncio.wait_for(drive(), time_limit)
    except TimeoutError:
        outcome["reason"] = f"timed out after {time_limit:.0f}s"
    except Exception as exc:  # the session's failure is the result, not the evaluation's
        outcome["reason"] = f"{type(exc).__name__}: {exc}"
    if log is not None:
        log.append("plain.ended", {**outcome, **totals})
    return PlainSession(
        outcome["status"], outcome["reason"], float(totals["cost"]), int(totals["turns"])
    )


def _as_data(message: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(message) and not isinstance(message, type):
        return dataclasses.asdict(message)
    return {"text": str(message)}
