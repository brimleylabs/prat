"""Running the web UI: runtime selection and the uvicorn server."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from prat.config import Settings
from prat.runtime import AgentRuntime, AgentTask, DoneEvent, Outcome, RuntimeEvent
from prat.runtime.auth import AuthError


class DeferredRuntime:
    """Builds the real runtime on first use and again after an auth failure.

    The server can then start (and show the settings page explaining what is
    wrong) even when authentication is not usable yet; a run started before it
    is fixed fails with the reason instead of crashing the server.
    """

    def __init__(self, name: str, factory: Callable[[], AgentRuntime]) -> None:
        self.name = name
        self._factory = factory
        self._inner: AgentRuntime | None = None

    def _get(self) -> AgentRuntime:
        if self._inner is None:
            self._inner = self._factory()
        return self._inner

    def run(self, task: AgentTask) -> AsyncIterator[RuntimeEvent]:
        return self._stream(lambda inner: inner.run(task))

    def resume(self, session_id: str, task: AgentTask) -> AsyncIterator[RuntimeEvent]:
        return self._stream(lambda inner: inner.resume(session_id, task))

    async def cancel(self, session_id: str) -> None:
        if self._inner is not None:
            await self._inner.cancel(session_id)

    async def _stream(
        self, start: Callable[[AgentRuntime], AsyncIterator[RuntimeEvent]]
    ) -> AsyncIterator[RuntimeEvent]:
        try:
            inner = self._get()
        except AuthError as exc:
            yield DoneEvent(Outcome.ERROR, f"authentication is not usable: {exc}")
            return
        async for event in start(inner):
            yield event


def run_server(
    settings: Settings,
    app: object,
    *,
    host: str,
    port: int,
) -> None:
    """Serve until interrupted (Ctrl+C)."""
    import uvicorn

    config = uvicorn.Config(
        app,  # type: ignore[arg-type]
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        server_header=False,
        proxy_headers=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    asyncio.run(server.serve())
