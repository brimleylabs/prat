"""Global and per-project concurrency limits for runs."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class Limiter:
    """Hands out run slots. A waiting run holds no global slot.

    The per-project slot is taken first, so a project at its limit cannot
    tie up global capacity that another project could use.
    """

    def __init__(self, global_limit: int, per_project: int) -> None:
        self.global_limit = global_limit
        self.per_project = per_project
        self._global = asyncio.Semaphore(global_limit)
        self._projects: dict[int, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(per_project)
        )
        self.running = 0
        self.peak = 0
        self.running_by_project: dict[int, int] = defaultdict(int)
        self.peak_by_project: dict[int, int] = defaultdict(int)

    @asynccontextmanager
    async def slot(self, project_id: int) -> AsyncIterator[None]:
        async with self._projects[project_id], self._global:
            self.running += 1
            self.peak = max(self.peak, self.running)
            self.running_by_project[project_id] += 1
            self.peak_by_project[project_id] = max(
                self.peak_by_project[project_id], self.running_by_project[project_id]
            )
            try:
                yield
            finally:
                self.running -= 1
                self.running_by_project[project_id] -= 1
