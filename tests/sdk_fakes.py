"""A scripted stand-in for ``ClaudeSDKClient`` (no subprocess, no network).

It speaks the real SDK message types and, for each tool use in its script,
calls the ``PreToolUse`` hook from the options exactly as Claude Code would,
then reports the tool result (or the denial) the way the CLI does.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

MODEL = "claude-haiku-5-5"
SESSION = "sess-123"


@dataclass
class Tool:
    """Ask to use a tool. Allowed Write/Edit calls really change files in cwd."""

    name: str
    input: dict[str, Any]
    id: str = "tu-1"


@dataclass
class Hang:
    """Block until cancelled."""


@dataclass
class Fail:
    error: Exception


def init(tools: list[str] | None = None, session_id: str = SESSION) -> SystemMessage:
    return SystemMessage(
        subtype="init",
        data={"session_id": session_id, "tools": tools or ["Read"], "model": MODEL},
    )


def say(text: str, *, message_id: str = "m-text", usage: dict[str, int] | None = None) -> Any:
    return AssistantMessage(
        content=[TextBlock(text)],
        model=MODEL,
        usage=usage,
        message_id=message_id,
        session_id=SESSION,
    )


def result(
    text: str = "done",
    *,
    subtype: str = "success",
    is_error: bool = False,
    total: float | None = 0.002,
    errors: list[str] | None = None,
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=10,
        duration_api_ms=8,
        is_error=is_error,
        num_turns=1,
        session_id=SESSION,
        total_cost_usd=total,
        result=text,
        errors=errors,
    )


@dataclass
class FakeClient:
    options: ClaudeAgentOptions
    script: list[Any]
    prompts: list[str] = field(default_factory=list)
    hook_outputs: list[dict[str, Any]] = field(default_factory=list)
    connected: bool = False
    interrupted: bool = False
    disconnected: bool = False
    fail_connect: Exception | None = None

    async def connect(self) -> None:
        if self.fail_connect is not None:
            raise self.fail_connect
        self.connected = True

    async def query(self, prompt: str) -> None:
        self.prompts.append(prompt)

    async def interrupt(self) -> None:
        self.interrupted = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def run_hook(self, name: str, tool_input: dict[str, Any], tool_id: str) -> dict[str, Any]:
        assert self.options.hooks is not None
        matcher = self.options.hooks["PreToolUse"][0]
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": SESSION,
            "transcript_path": "",
            "cwd": str(self.options.cwd),
            "tool_name": name,
            "tool_input": tool_input,
            "tool_use_id": tool_id,
        }
        out = dict(await matcher.hooks[0](payload, tool_id, {"signal": None}))  # type: ignore[arg-type]
        self.hook_outputs.append(out)
        return out

    def _execute(self, tool: Tool) -> str:
        cwd = Path(str(self.options.cwd))
        if tool.name == "Write":
            target = cwd / tool.input["file_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(tool.input.get("content", ""), encoding="utf-8", newline="")
            return "written"
        if tool.name == "Read":
            return (cwd / tool.input["file_path"]).read_text(encoding="utf-8")
        return "ok"

    async def receive_response(self) -> AsyncIterator[Any]:
        for step in self.script:
            if isinstance(step, Hang):
                await asyncio.Event().wait()
            elif isinstance(step, Fail):
                raise step.error
            elif isinstance(step, Tool):
                yield AssistantMessage(
                    content=[ToolUseBlock(step.id, step.name, step.input)],
                    model=MODEL,
                    message_id=f"m-{step.id}",
                    session_id=SESSION,
                )
                out = await self.run_hook(step.name, step.input, step.id)
                spec = out["hookSpecificOutput"]
                if spec["permissionDecision"] == "allow":
                    yield UserMessage(
                        content=[ToolResultBlock(step.id, self._execute(step), False)]
                    )
                else:
                    reason = spec["permissionDecisionReason"]
                    yield UserMessage(content=[ToolResultBlock(step.id, reason, True)])
            else:
                yield step


class FakeFactory:
    """Client factory recording every client it builds."""

    def __init__(
        self,
        script: list[Any] | Callable[[ClaudeAgentOptions], list[Any]],
        *,
        fail_connect: Exception | None = None,
    ) -> None:
        self.script = script
        self.fail_connect = fail_connect
        self.clients: list[FakeClient] = []

    def __call__(self, options: ClaudeAgentOptions) -> FakeClient:
        script = self.script(options) if callable(self.script) else list(self.script)
        client = FakeClient(options, script, fail_connect=self.fail_connect)
        self.clients.append(client)
        return client
