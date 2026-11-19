"""Racing builders: two agents build the same card at once, and the first to finish with a change
is kept.

How long an agent takes on a card varies a lot from one session to the next (the same card can
take one session twice as long as another). With ``race_builders = 2`` PRAT starts a second
builder, the rival, in a worktree of its own next to the card's, from the same commit, under the
same policy and the same instructions. The first builder to finish with a completed outcome and
a change wins; the other is stopped. When the rival wins, the card's branch takes its commit, so
everything after (gates, reviewer, lessons) sees one card and one change.

A builder that finishes without a change, blocked or failed does not win: the race waits for the
other. Cards with their own database branch never race (both builders would write the same data),
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
class Race:
    runtime: AgentRuntime
    task: AgentTask
    worktrees: WorktreeManager
    agent_path: Path
    lead_path: Path
    path: Path
    branch: str
    start: str
    on_event: Callable[[RuntimeEvent], None] | None = None
    sandbox: Sandbox | None = None
    cost_usd: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    _rival_head: str | None = None
    _rival_session: str | None = None

    def _record(self, event: RuntimeEvent) -> None:
        if isinstance(event, SessionStarted):
            self._rival_session = event.session_id
        if self.on_event is not None:
            self.on_event(event)

    async def _abandon(self, session_id: str | None) -> None:
        """End the loser's session at once where the runtime can; its work is thrown away."""
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
        """Run the lead (the card's own session loop) against the rival. Returns the rival's
        output when the rival won, ``None`` when the lead's result stands."""
        started = time.monotonic()
        lead_task = asyncio.ensure_future(lead())
        rival_task = asyncio.ensure_future(collect(self.runtime, self.task, on_event=self._record))
        pending: set[asyncio.Future[Any]] = {lead_task, rival_task}
        rival: SessionOutput | None = None
        lead_good = rival_good = False
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                if lead_task in done:
                    lead_task.result()
                    lead_good = lead_outcome() is Outcome.COMPLETED and await asyncio.to_thread(
                        has_change, self.lead_path, self.start
                    )
                    if lead_good:
                        self.detail["winner"] = "lead"
                        await self._abandon(self._rival_session)
                        break
                if rival_task in done:
                    rival = rival_task.result()
                    self.cost_usd = rival.cost_usd
                    rival_good = (
                        rival.outcome is Outcome.COMPLETED
                        and not rival.blocked_reason
                        and await asyncio.to_thread(has_change, self.path, self.start)
                    )
                    if rival_good:
                        self.detail["winner"] = "rival"
                        if lead_session is not None and not lead_task.done():
                            await self._abandon(lead_session())
                        break
            self.detail.setdefault("winner", "none")
        except BaseException:
            await _stop(lead_task, rival_task)
            raise
        self.detail["seconds"] = round(time.monotonic() - started, 1)
        await _stop(*pending)
        if rival is None and rival_task.done() and not rival_task.cancelled():
            with contextlib.suppress(BaseException):
                rival = rival_task.result()
                self.cost_usd = rival.cost_usd
        self.detail["rival_cost_usd"] = round(self.cost_usd, 6)
        return rival if rival_good and not lead_good else None

    def adopt(self, lead_path: Path) -> None:
        """The rival won: commit its work and move the card's worktree (and branch) to it."""
        head = self.worktrees.safety_commit(self.path, "PRAT: the racing builder's work")
        self._rival_head = head or Git(self.path).head()
        lead = Git(lead_path)
        lead.run("reset", "--hard", "--quiet", self._rival_head)
        lead.run("clean", "-fd", "--quiet")
        self.detail["adopted"] = self._rival_head

    def cleanup(self) -> None:
        if self.sandbox is not None:
            with contextlib.suppress(Exception):
                self.sandbox.cleanup()
        with contextlib.suppress(Exception):
            self.worktrees.remove_worktree(self.agent_path, self.path)
        Git(self.agent_path).run("branch", "-D", "--quiet", self.branch, check=False)


async def _stop(*tasks: asyncio.Future[Any]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
