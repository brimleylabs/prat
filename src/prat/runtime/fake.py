"""A deterministic runtime driven by a script, for tests and demos.

A script is a list of steps (or ``{"session_id": ..., "steps": [...]}``)::

    [
      {"op": "text", "text": "Reading the code"},
      {"op": "write", "path": "hello.py", "content": "print('hi')\\n"},
      {"op": "bash", "command": "{python} hello.py"},
      {"op": "finish", "message": "Added hello.py"}
    ]

Operations: ``text``, ``read``, ``write``, ``edit``, ``delete``, ``bash``,
``mcp``, ``cost``, ``wait``, ``finish``, ``block``, ``exceed_budget``,
``error`` and ``crash``. Every tool operation goes through the task's policy
callback before it runs, exactly as a real adapter must. ``{python}`` in a
command is replaced with the current interpreter. A tool step with
``"dry": true`` asks the policy and stops there: the evaluation harness
replays recorded commands this way (prat.evals).

``crash`` raises :class:`FakeCrash`, a ``BaseException`` that models the PRAT
process dying: nothing in the engine handles it, so the database is left
mid-run, as after a real crash. Session progress is stored under
``state_dir`` so that :meth:`FakeRuntime.resume` continues after the crash
point, the way a real agent resumes a session by id.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from prat.policy import ToolCall
from prat.policy.shell import ShellParseError, split_commands
from prat.proc import run_tree
from prat.runtime.base import (
    AgentTask,
    BlockedEvent,
    CostEvent,
    DoneEvent,
    Outcome,
    RuntimeEvent,
    SessionStarted,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
)

Op = Literal[
    "text",
    "read",
    "write",
    "edit",
    "delete",
    "bash",
    "mcp",
    "web",
    "cost",
    "wait",
    "finish",
    "block",
    "exceed_budget",
    "error",
    "crash",
]


class FakeCrash(BaseException):
    """Simulates the PRAT process dying in the middle of a run."""


class FakeStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Op
    text: str | None = None
    path: str | None = None
    content: str | None = None
    old: str | None = None
    new: str | None = None
    command: str | None = None
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    usd: float | None = None
    seconds: float | None = None
    message: str | None = None
    reason: str | None = None
    # Ask the policy about the call but never carry it out.
    dry: bool = False


class FakeScript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = None
    steps: list[FakeStep]

    @classmethod
    def parse(cls, data: Any) -> FakeScript:
        if isinstance(data, list):
            data = {"steps": data}
        return cls.model_validate(data)

    @classmethod
    def load(cls, path: Path) -> FakeScript:
        return cls.parse(json.loads(path.read_text(encoding="utf-8")))


ScriptSource = FakeScript | Callable[[AgentTask], FakeScript]


class FakeRuntime:
    name = "fake"

    def __init__(
        self,
        script: ScriptSource | list[dict[str, Any]],
        *,
        state_dir: Path | None = None,
        command_timeout: float = 120.0,
    ) -> None:
        self._script: ScriptSource = (
            FakeScript.parse(script) if isinstance(script, list) else script
        )
        self.state_dir = state_dir
        self.command_timeout = command_timeout
        self._cancel: dict[str, asyncio.Event] = {}
        self.cancelled: set[str] = set()
        self.sessions: list[str] = []
        self.active = 0
        self.peak_active = 0
        self.executed: list[tuple[str, str]] = []

    # Protocol ------------------------------------------------------------

    def run(self, task: AgentTask) -> AsyncIterator[RuntimeEvent]:
        script = self._resolve(task)
        session_id = script.session_id or f"fake-{uuid.uuid4().hex[:12]}"
        return self._drive(session_id, task, script, cursor=0, resumed=False)

    def resume(self, session_id: str, task: AgentTask) -> AsyncIterator[RuntimeEvent]:
        script = self._resolve(task)
        cursor = self._load_cursor(session_id)
        # Skip the step that was in flight if it was the simulated crash.
        if cursor < len(script.steps) and script.steps[cursor].op == "crash":
            cursor += 1
        return self._drive(session_id, task, script, cursor=cursor, resumed=True)

    async def cancel(self, session_id: str) -> None:
        self.cancelled.add(session_id)
        event = self._cancel.get(session_id)
        if event is not None:
            event.set()

    # Internals -------------------------------------------------------------

    def _resolve(self, task: AgentTask) -> FakeScript:
        return self._script(task) if callable(self._script) else self._script

    def _state_file(self, session_id: str) -> Path | None:
        if self.state_dir is None:
            return None
        return self.state_dir / f"{session_id}.json"

    def _load_cursor(self, session_id: str) -> int:
        file = self._state_file(session_id)
        if file is None or not file.exists():
            return 0
        data = json.loads(file.read_text(encoding="utf-8"))
        return int(data.get("cursor", 0))

    def _save_cursor(self, session_id: str, cursor: int) -> None:
        file = self._state_file(session_id)
        if file is None:
            return
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps({"cursor": cursor}), encoding="utf-8")

    async def _drive(
        self, session_id: str, task: AgentTask, script: FakeScript, *, cursor: int, resumed: bool
    ) -> AsyncIterator[RuntimeEvent]:
        cancel_event = self._cancel.setdefault(session_id, asyncio.Event())
        self.sessions.append(session_id)
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        turns = 0
        try:
            yield SessionStarted(session_id, resumed=resumed)
            index = cursor
            while index < len(script.steps):
                if cancel_event.is_set():
                    yield DoneEvent(Outcome.CANCELLED, "cancelled")
                    return
                self._save_cursor(session_id, index)
                step = script.steps[index]
                if step.op in ("read", "write", "edit", "delete", "bash", "mcp", "web"):
                    turns += 1
                    if task.budget.max_turns is not None and turns > task.budget.max_turns:
                        yield DoneEvent(Outcome.BUDGET_EXCEEDED, "turn limit reached")
                        return
                async for event in self._step(session_id, index, step, task, cancel_event):
                    yield event
                    if isinstance(event, DoneEvent):
                        self._save_cursor(session_id, index + 1)
                        return
                index += 1
            self._save_cursor(session_id, index)
            yield DoneEvent(Outcome.COMPLETED, "script finished")
        finally:
            self.active -= 1

    async def _step(
        self,
        session_id: str,
        index: int,
        step: FakeStep,
        task: AgentTask,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[RuntimeEvent]:
        call_id = f"{session_id}-{index}"
        op = step.op
        if op == "text":
            yield TextEvent(step.text or "")
        elif op == "cost":
            yield CostEvent(usd=step.usd or 0.0)
        elif op == "exceed_budget":
            limit = task.budget.max_usd or 0.0
            yield CostEvent(usd=limit + 1.0)
        elif op == "wait":
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(cancel_event.wait(), timeout=step.seconds or 0.0)
            if cancel_event.is_set():
                yield DoneEvent(Outcome.CANCELLED, "cancelled while waiting")
        elif op == "finish":
            yield DoneEvent(Outcome.COMPLETED, step.message or "done")
        elif op == "block":
            reason = step.reason or "blocked"
            yield BlockedEvent(reason)
            yield DoneEvent(Outcome.BLOCKED, reason)
        elif op == "error":
            yield DoneEvent(Outcome.ERROR, step.message or "agent error")
        elif op == "crash":
            raise FakeCrash(f"simulated crash at step {index}")
        else:
            async for event in self._tool(call_id, step, task):
                yield event

    async def _tool(
        self, call_id: str, step: FakeStep, task: AgentTask
    ) -> AsyncIterator[RuntimeEvent]:
        call, tool_input = self._tool_call(step)
        yield ToolCallEvent(call_id, step.op, tool_input)
        if step.op == "web" and (step.tool or "") not in task.allowed_tools:
            # Mirrors the real adapter: a tool outside the task's set is never offered.
            reason = f"tool {step.tool} is not enabled for this agent"
            yield ToolResultEvent(call_id, ok=False, denied=True, reason=reason)
            return
        decision = task.policy(call)
        if not decision.allowed:
            yield ToolResultEvent(call_id, ok=False, denied=True, reason=decision.reason)
            return
        if step.dry:
            yield ToolResultEvent(call_id, ok=True, output="allowed; not carried out (replay)")
            return
        try:
            ok, output = await self._execute(step, task.workdir, task.env, task.shell)
        except OSError as exc:
            ok, output = False, f"{type(exc).__name__}: {exc}"
        self.executed.append((step.op, step.path or step.command or step.tool or ""))
        yield ToolResultEvent(call_id, ok=ok, output=output)

    def _tool_call(self, step: FakeStep) -> tuple[ToolCall, Mapping[str, Any]]:
        if step.op == "read":
            return ToolCall.read(step.path or ""), {"path": step.path}
        if step.op == "write":
            return ToolCall.write(step.path or "", step.content or ""), {
                "path": step.path,
                "content": step.content,
            }
        if step.op == "edit":
            return ToolCall.write(step.path or "", step.new or ""), {
                "path": step.path,
                "old": step.old,
                "new": step.new,
            }
        if step.op == "delete":
            # Removing a file is a write to that path.
            return ToolCall.write(step.path or "", ""), {"path": step.path, "delete": True}
        if step.op == "bash":
            command = self._command(step.command or "")
            return ToolCall.bash(command), {"command": command}
        if step.op == "web":
            return ToolCall.web(step.tool or "", step.arguments), {
                "tool": step.tool,
                "arguments": step.arguments,
            }
        return ToolCall.mcp(step.tool or "", step.arguments), {
            "tool": step.tool,
            "arguments": step.arguments,
        }

    @staticmethod
    def _command(command: str) -> str:
        return command.replace("{python}", shlex.quote(Path(sys.executable).as_posix()))

    async def _execute(
        self,
        step: FakeStep,
        workdir: Path,
        env: Mapping[str, str] | None = None,
        shell: Callable[[str], str] | None = None,
    ) -> tuple[bool, str]:
        if step.op == "read":
            target = workdir / (step.path or "")
            return True, target.read_text(encoding="utf-8", errors="replace")
        if step.op == "write":
            target = workdir / (step.path or "")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(step.content or "", encoding="utf-8", newline="")
            return True, f"wrote {len(step.content or '')} characters"
        if step.op == "edit":
            target = workdir / (step.path or "")
            text = target.read_text(encoding="utf-8")
            if step.old is None or step.old not in text:
                return False, "old text not found"
            target.write_text(
                text.replace(step.old, step.new or "", 1), encoding="utf-8", newline=""
            )
            return True, "edited"
        if step.op == "delete":
            target = workdir / (step.path or "")
            if not target.is_file():
                return False, "no such file"
            target.unlink()
            return True, "deleted"
        if step.op == "bash":
            command = self._command(step.command or "")
            if shell is not None:
                # As the Claude runtime does: the approved command runs in the sandbox.
                command = shlex.join(["sh", "-c", shell(command)])
            return await asyncio.to_thread(self._run_command, command, workdir, env)
        if step.op == "web":
            # The scripted page or search result; nothing leaves the machine.
            return True, step.content or f"fake {step.tool} returned nothing"
        return True, f"fake MCP tool {step.tool} returned nothing"

    def _run_command(
        self, command: str, workdir: Path, env: Mapping[str, str] | None = None
    ) -> tuple[bool, str]:
        try:
            parts = split_commands(command)
        except ShellParseError as exc:
            return False, str(exc)
        if len(parts) != 1 or parts[0].redirects or parts[0].assignments:
            return False, "the fake runtime only executes simple commands"
        try:
            proc = run_tree(
                parts[0].argv,
                cwd=workdir,
                timeout=self.command_timeout,
                env={**os.environ, **env} if env else None,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"{type(exc).__name__}: {exc}"
        output = (proc.stdout + proc.stderr)[-4000:]
        return proc.returncode == 0, f"exit {proc.returncode}\n{output}"
