"""Racing builders: several agents build the same card at once, and the first to finish with a
change is kept.

How long an agent takes on a card varies a lot from one session to the next (the same card can
take one session twice as long as another). With ``race_builders`` above 1, PRAT starts rival
builders, each in a worktree of its own next to the card's, from the same commit, under the same
policy and the same instructions. The first builder to finish with a completed outcome and a
change wins; the others are ended at once. When a rival wins, the card's branch takes its
commit, so everything after (gates, reviewer, lessons) sees one card and one change.

A builder that finishes without a change, blocked or failed does not win: the race waits for the
others. Cards with their own database branch never race (the builders would write the same data),
nor do resumed sessions, setup and infra cards.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prat.git import Git, WorktreeManager
from prat.runtime.base import AgentRuntime, AgentTask, Outcome, RuntimeEvent, SessionStarted
from prat.runtime.session import SessionOutput, collect
from prat.sandbox import Sandbox


def has_change(worktree: Path, start: str) -> bool:
    git = Git(worktree)
    if git.head() != start:
        return True
    return bool(git.run("status", "--porcelain", check=False).stdout.strip())


@dataclass
class Rival:
    """One rival builder: its name in the journal ("rival", "rival2", ...), task and worktree."""

    name: str
    task: AgentTask
    path: Path
    branch: str
    on_event: Callable[[RuntimeEvent], None] | None = None
    sandbox: Sandbox | None = None
    session: str | None = None
    output: SessionOutput | None = None

    def record(self, event: RuntimeEvent) -> None:
        if isinstance(event, SessionStarted):
            self.session = event.session_id
        if self.on_event is not None:
            self.on_event(event)


@dataclass
class Race:
    runtime: AgentRuntime
    worktrees: WorktreeManager
    agent_path: Path
    lead_path: Path
    start: str
    rivals: list[Rival]
    cost_usd: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    winner: Rival | None = None

    async def _abandon(self, session_id: str | None) -> None:
        """End a loser's session at once where the runtime can; its work is thrown away."""
        abandon = getattr(self.runtime, "abandon", None)
        if session_id and abandon is not None:
            with contextlib.suppress(Exception):
                await abandon(session_id)

    async def run(
        self,
        lead: Callable[[], Awaitable[None]],
        lead_outcome: Callable[[], Outcome | None],
        lead_session: Callable[[], str | None] | None = None,
    ) -> SessionOutput | None:
        """Run the lead (the card's own session loop) against the rivals. Returns the winning
        rival's output, or ``None`` when the lead's result stands."""
        started = time.monotonic()
        lead_task = asyncio.ensure_future(lead())
        tasks: dict[asyncio.Future[Any], Rival | None] = {lead_task: None}
        for each in self.rivals:
            tasks[asyncio.ensure_future(collect(self.runtime, each.task, on_event=each.record))] = (
                each
            )
        pending: set[asyncio.Future[Any]] = set(tasks)
        lead_won = False
        try:
            while pending and not lead_won and self.winner is None:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for future in done:
                    rival = tasks[future]
                    if rival is None:
                        future.result()
                        if lead_outcome() is Outcome.COMPLETED and await asyncio.to_thread(
                            has_change, self.lead_path, self.start
                        ):
                            lead_won = True
                            break
                        continue
                    rival.output = future.result()
                    out = rival.output
                    if (
                        out.outcome is Outcome.COMPLETED
                        and not out.blocked_reason
                        and await asyncio.to_thread(has_change, rival.path, self.start)
                    ):
                        self.winner = rival
                        break
            if lead_won:
                self.detail["winner"] = "lead"
            elif self.winner is not None:
                self.detail["winner"] = self.winner.name
            else:
                self.detail["winner"] = "none"
            # The losers are ended at once: their work is thrown away.
            for rival in self.rivals:
                if rival is not self.winner and rival.output is None:
                    await self._abandon(rival.session)
            if self.winner is not None and lead_session is not None and not lead_task.done():
                await self._abandon(lead_session())
        except BaseException:
            await _stop(*tasks)
            raise
        self.detail["seconds"] = round(time.monotonic() - started, 1)
        await _stop(*pending)
        for future, rival in tasks.items():
            if (
                rival is not None
                and rival.output is None
                and future.done()
                and not future.cancelled()
                and future.exception() is None
            ):
                rival.output = future.result()
        self.cost_usd = sum(r.output.cost_usd for r in self.rivals if r.output is not None)
        self.detail["rival_cost_usd"] = round(self.cost_usd, 6)
        self.detail["rivals"] = len(self.rivals)
        return self.winner.output if self.winner is not None else None

    def adopt(self, lead_path: Path) -> None:
        """A rival won: commit its work and move the card's worktree (and branch) to it."""
        if self.winner is None:
            return
        head = self.worktrees.safety_commit(self.winner.path, "PRAT: the racing builder's work")
        commit = head or Git(self.winner.path).head()
        lead = Git(lead_path)
        lead.run("reset", "--hard", "--quiet", commit)
        lead.run("clean", "-fd", "--quiet")
        self.detail["adopted"] = commit

    def cleanup(self) -> None:
        for rival in self.rivals:
            if rival.sandbox is not None:
                with contextlib.suppress(Exception):
                    rival.sandbox.cleanup()
            with contextlib.suppress(Exception):
                self.worktrees.remove_worktree(self.agent_path, rival.path)
            Git(self.agent_path).run("branch", "-D", "--quiet", rival.branch, check=False)


async def _stop(*tasks: asyncio.Future[Any]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
