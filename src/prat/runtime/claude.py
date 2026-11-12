"""Agent runtime backed by the Claude Agent SDK (``claude-agent-sdk``).

The SDK runs the unmodified Claude Code executable as a subprocess. This
adapter:

- restricts the tool set with the SDK's ``tools`` option (the base set the
  model can see at all) and lists every other built-in tool in
  ``disallowed_tools``. ``allowed_tools`` is deliberately not used: it only
  pre-approves tools, it does not restrict them;
- sends every tool call, MCP tools included, through the task's policy
  callback from a ``PreToolUse`` hook. The hook fails closed
  (``on_failure="block"``), and a ``can_use_tool`` fallback denies anything
  that would otherwise reach an interactive permission prompt. A denial goes
  back to the model as the tool's error with the reason;
- isolates the session from the machine's Claude Code configuration
  (``setting_sources=[]``, ``strict_mcp_config``), so user hooks, permission
  rules, plugins and MCP servers do not leak into PRAT runs;
- maps SDK messages to PRAT runtime events, estimates cost per response and
  reconciles it with the session's own total, captures the session id for
  resume, and recognises the BLOCKED protocol (a final line
  ``BLOCKED: <reason>``);
- cancels for real: ``cancel`` interrupts the session and the stream then
  disconnects, which terminates the Claude Code process.

Authentication is decided by :mod:`prat.runtime.auth`; this module only
passes the resulting environment overrides to the subprocess.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Protocol, cast

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    PermissionResult,
    PermissionResultDeny,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from prat.model_registry import estimate_cost
from prat.policy import Decision, ToolCall
from prat.runtime.base import (
    TOOL_BASH,
    TOOL_EDIT,
    TOOL_GLOB,
    TOOL_GREP,
    TOOL_MCP,
    TOOL_READ,
    TOOL_WEB_FETCH,
    TOOL_WEB_SEARCH,
    TOOL_WRITE,
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

if TYPE_CHECKING:
    from prat.config import Settings

# PRAT tool names -> Claude Code built-in tool names.
SDK_TOOLS: Mapping[str, tuple[str, ...]] = {
    TOOL_READ: ("Read",),
    TOOL_GLOB: ("Glob",),
    TOOL_GREP: ("Grep",),
    TOOL_WRITE: ("Write",),
    TOOL_EDIT: ("Edit",),
    TOOL_BASH: ("Bash",),
    # Offered only to tasks whose tool set includes them (the researcher).
    TOOL_WEB_SEARCH: ("WebSearch",),
    TOOL_WEB_FETCH: ("WebFetch",),
}
# Every built-in tool PRAT knows of. Whatever a task does not enable is listed
# in disallowed_tools as well as being left out of ``tools``.
KNOWN_BUILTIN_TOOLS: tuple[str, ...] = (
    "Agent",
    "AskUserQuestion",
    "Bash",
    "BashOutput",
    "Edit",
    "EnterPlanMode",
    "EnterWorktree",
    "ExitPlanMode",
    "ExitWorktree",
    "Glob",
    "Grep",
    "KillShell",
    "LS",
    "Monitor",
    "MultiEdit",
    "NotebookEdit",
    "NotebookRead",
    "Read",
    "Skill",
    "SlashCommand",
    "Task",
    "TodoWrite",
    "ToolSearch",
    "WebFetch",
    "WebSearch",
    "Workflow",
    "Write",
)

RESUME_PROMPT = (
    "PRAT was restarted while you were working. Continue the same task from where you "
    "stopped. Check the current state of the worktree (git status, git log) first."
)
DENIAL_PREFIX = "PRAT policy denied this call: "
BLOCKED_LINE = re.compile(r"^\s*[*_]*BLOCKED[*_]*\s*:\s*[*_]*\s*(?P<reason>.+?)\s*$", re.MULTILINE)
_GLOB_CHARS = re.compile(r"[*?\[{]")


class SdkClient(Protocol):
    """The part of ``ClaudeSDKClient`` PRAT uses. Tests substitute a fake."""

    async def connect(self) -> None: ...

    async def query(self, prompt: str) -> None: ...

    def receive_response(self) -> AsyncIterator[Any]: ...

    async def interrupt(self) -> None: ...

    async def disconnect(self) -> None: ...


ClientFactory = Callable[[ClaudeAgentOptions], SdkClient]


def default_client_factory(options: ClaudeAgentOptions) -> SdkClient:
    return ClaudeSDKClient(options=options)


def _blank_prat_settings() -> dict[str, str]:
    """Empty values for PRAT's own ``PRAT_*`` variables (database URL, home and the like)."""
    return {key: "" for key in os.environ if key.upper().startswith("PRAT_")}


@dataclass(frozen=True)
class ClaudeConfig:
    # Environment overrides for the Claude Code process (from prat.runtime.auth).
    env: Mapping[str, str] = field(default_factory=dict)
    cli_path: Path | None = None
    hook_timeout_seconds: float = 120.0
    interrupt_timeout_seconds: float = 5.0
    output_limit: int = 4000


# Tool call mapping -------------------------------------------------------------


def blocked_reason(text: str | None) -> str | None:
    """The reason from the last ``BLOCKED: <reason>`` line, if any."""
    if not text:
        return None
    matches = list(BLOCKED_LINE.finditer(text))
    return matches[-1].group("reason").strip() if matches else None


def _glob_base(pattern: str) -> str | None:
    """For a glob that reaches outside the search root, the literal part to check."""
    text = pattern.replace("\\", "/")
    escapes = (
        text.startswith(("/", "~", ".."))
        or re.match(r"^[A-Za-z]:/", text) is not None
        or "/../" in text
    )
    if not escapes:
        return None
    head = _GLOB_CHARS.split(text, maxsplit=1)[0]
    if head == text:
        return text
    if head.endswith("/"):
        return head.rstrip("/") or "/"
    return str(PurePosixPath(head).parent) if "/" in head else (head or None)


def tool_calls_for(tool_name: str, tool_input: Mapping[str, Any]) -> list[ToolCall] | Decision:
    """Translate a Claude Code tool call into PRAT policy calls (all must pass)."""

    def text(key: str) -> str | None:
        value = tool_input.get(key)
        return value if isinstance(value, str) else None

    if tool_name == "Read":
        return [ToolCall.read(text("file_path") or "")]
    if tool_name == "Write":
        return [ToolCall.write(text("file_path") or "", text("content") or "")]
    if tool_name == "Edit":
        return [ToolCall.write(text("file_path") or "", text("new_string") or "")]
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        content = "\n".join(
            str(e.get("new_string", "")) for e in edits or [] if isinstance(e, Mapping)
        )
        return [ToolCall.write(text("file_path") or "", content)]
    if tool_name == "NotebookEdit":
        return [ToolCall.write(text("notebook_path") or "", text("new_source") or "")]
    if tool_name == "Bash":
        if tool_input.get("run_in_background"):
            return Decision.deny("background commands are not allowed: they outlive the run")
        return [ToolCall.bash(text("command") or "")]
    if tool_name in ("Glob", "Grep"):
        calls = [ToolCall.read(text("path") or ".")]
        # Glob's pattern is a path pattern; Grep's pattern is a regex, its glob a path.
        base = _glob_base(text("pattern" if tool_name == "Glob" else "glob") or "")
        if base:
            calls.append(ToolCall.read(base))
        return calls
    if tool_name == "WebSearch":
        return [ToolCall.web(TOOL_WEB_SEARCH, {"query": text("query") or ""})]
    if tool_name == "WebFetch":
        return [ToolCall.web(TOOL_WEB_FETCH, {"url": text("url") or ""})]
    if tool_name.startswith("mcp__"):
        return [ToolCall.mcp(tool_name, dict(tool_input))]
    return Decision.deny(f"tool {tool_name} is not available to PRAT agents")


def judge(
    policy: Callable[[ToolCall], Decision],
    tool_name: str,
    tool_input: Any,
    enabled: frozenset[str] | None = None,
) -> Decision:
    """Decide a Claude Code tool call with PRAT's policy. Never raises.

    ``enabled`` is the task's PRAT tool set; a call to anything outside it is
    denied even if the tool somehow reached the model.
    """
    try:
        if enabled is not None:
            if tool_name.startswith("mcp__"):
                if TOOL_MCP not in enabled:
                    return Decision.deny("MCP tools are not enabled for this agent")
            elif tool_name not in sdk_tool_names(enabled):
                return Decision.deny(f"tool {tool_name} is not enabled for this agent")
        args = tool_input if isinstance(tool_input, Mapping) else {}
        calls = tool_calls_for(tool_name, args)
        if isinstance(calls, Decision):
            return calls
        decision = Decision.allow()
        for call in calls:
            decision = policy(call)
            if not decision.allowed:
                return decision
        return decision
    except Exception as exc:  # fail closed
        return Decision.deny(f"policy check failed: {type(exc).__name__}: {exc}")


def sdk_tool_names(allowed: frozenset[str]) -> list[str]:
    names: list[str] = []
    for prat_name in sorted(allowed):
        names.extend(SDK_TOOLS.get(prat_name, ()))
    return names


# Message mapping ---------------------------------------------------------------


def _result_text(content: Any, limit: int) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            else:
                parts.append(json.dumps(item, default=str))
        text = "\n".join(parts)
    else:
        text = json.dumps(content, default=str)
    return text if len(text) <= limit else text[:limit] + " [trimmed]"


@dataclass
class MessageMapper:
    """Turns SDK messages into PRAT runtime events. One per session."""

    resumed: bool = False
    output_limit: int = 4000
    denials: dict[str, str] = field(default_factory=dict)
    session_id: str | None = None
    estimated_usd: float = 0.0
    last_text: str = ""
    _seen_messages: set[str] = field(default_factory=set)

    def map(self, message: Any) -> list[RuntimeEvent]:
        if isinstance(message, SystemMessage):
            return self._system(message)
        if isinstance(message, AssistantMessage):
            return self._assistant(message)
        if isinstance(message, UserMessage):
            return self._user(message)
        if isinstance(message, ResultMessage):
            return self._result(message)
        return []

    def _session(self, session_id: str | None, **extra: Any) -> list[RuntimeEvent]:
        if self.session_id is not None or not session_id:
            return []
        self.session_id = session_id
        return [SessionStarted(session_id, resumed=self.resumed, **extra)]

    def _system(self, message: SystemMessage) -> list[RuntimeEvent]:
        if message.subtype != "init":
            return []
        data = message.data
        tools = data.get("tools")
        return self._session(
            data.get("session_id"),
            tools=tuple(str(t) for t in tools) if isinstance(tools, list) else (),
            model=str(data.get("model") or ""),
        )

    def _assistant(self, message: AssistantMessage) -> list[RuntimeEvent]:
        events: list[RuntimeEvent] = list(self._session(message.session_id))
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                self.last_text = block.text
                events.append(TextEvent(block.text))
            elif isinstance(block, ToolUseBlock):
                events.append(ToolCallEvent(block.id, block.name, dict(block.input)))
        key = message.message_id or message.uuid
        if message.usage and (key is None or key not in self._seen_messages):
            if key is not None:
                self._seen_messages.add(key)
            usd = estimate_cost(message.model, message.usage)
            if usd > 0:
                self.estimated_usd += usd
                events.append(
                    CostEvent(
                        usd=usd,
                        input_tokens=int(message.usage.get("input_tokens") or 0),
                        output_tokens=int(message.usage.get("output_tokens") or 0),
                    )
                )
        return events

    def _user(self, message: UserMessage) -> list[RuntimeEvent]:
        if isinstance(message.content, str):
            return []
        events: list[RuntimeEvent] = []
        for block in message.content:
            if not isinstance(block, ToolResultBlock):
                continue
            reason = self.denials.pop(block.tool_use_id, None)
            if reason is not None:
                events.append(
                    ToolResultEvent(block.tool_use_id, ok=False, denied=True, reason=reason)
                )
                continue
            events.append(
                ToolResultEvent(
                    block.tool_use_id,
                    ok=not block.is_error,
                    output=_result_text(block.content, self.output_limit),
                )
            )
        return events

    def _result(self, message: ResultMessage) -> list[RuntimeEvent]:
        events: list[RuntimeEvent] = list(self._session(message.session_id))
        if message.total_cost_usd is not None:
            delta = float(message.total_cost_usd) - self.estimated_usd
            if abs(delta) > 1e-9:
                # Reconcile the running estimate with the session's own total.
                self.estimated_usd += delta
                events.append(CostEvent(usd=delta))
        subtype = message.subtype or ""
        text = message.result or self.last_text
        if "budget" in subtype:
            events.append(DoneEvent(Outcome.BUDGET_EXCEEDED, "the session's budget was reached"))
        elif "max_turns" in subtype:
            events.append(DoneEvent(Outcome.BUDGET_EXCEEDED, "turn limit reached"))
        elif message.is_error or subtype != "success":
            detail = "; ".join(message.errors or []) or message.result or subtype or "error"
            events.append(DoneEvent(Outcome.ERROR, detail[: self.output_limit]))
        else:
            reason = blocked_reason(text)
            if reason is not None:
                events.append(BlockedEvent(reason))
                events.append(DoneEvent(Outcome.BLOCKED, reason))
            else:
                events.append(DoneEvent(Outcome.COMPLETED, text or ""))
        return events


# Runtime -------------------------------------------------------------------------


_END = object()
_CANCELLED = object()


@dataclass
class _Live:
    cancel: asyncio.Event
    client: SdkClient | None = None
    session_id: str | None = None


class ClaudeRuntime:
    """:class:`prat.runtime.AgentRuntime` implementation on the Claude Agent SDK."""

    name = "claude"

    def __init__(
        self,
        config: ClaudeConfig | None = None,
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.config = config or ClaudeConfig()
        self._factory: ClientFactory = client_factory or default_client_factory
        self._live: dict[str, _Live] = {}
        self._cancelled: set[str] = set()
        self._stderr: deque[str] = deque(maxlen=20)

    @classmethod
    def from_settings(cls, settings: Settings) -> ClaudeRuntime:
        """Build the runtime, failing fast if the configured auth mode is unusable."""
        from prat.runtime.auth import check_auth, runtime_env

        check_auth(settings).require()
        return cls(ClaudeConfig(env=runtime_env(settings), cli_path=settings.claude_cli_path))

    # Options ---------------------------------------------------------------

    def build_options(
        self,
        task: AgentTask,
        *,
        resume: str | None = None,
        denials: dict[str, str] | None = None,
    ) -> ClaudeAgentOptions:
        enabled = sdk_tool_names(task.allowed_tools)
        disallowed = [name for name in KNOWN_BUILTIN_TOOLS if name not in enabled]
        recorded = denials if denials is not None else {}
        policy = task.policy

        async def pre_tool_use(
            input_data: HookInput, tool_use_id: str | None, context: HookContext
        ) -> HookJSONOutput:
            data = cast(Mapping[str, Any], input_data)
            name = str(data.get("tool_name") or "")
            decision = judge(policy, name, data.get("tool_input"), task.allowed_tools)
            if decision.allowed:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "permissionDecisionReason": decision.reason,
                    }
                }
            call_id = tool_use_id or str(data.get("tool_use_id") or "")
            if call_id:
                recorded[call_id] = decision.reason
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": DENIAL_PREFIX + decision.reason,
                }
            }

        async def can_use_tool(
            tool_name: str, tool_input: dict[str, Any], context: ToolPermissionContext
        ) -> PermissionResult:
            # Only reached when something bypassed the hook; never approve here.
            return PermissionResultDeny(
                message=DENIAL_PREFIX + f"{tool_name} was not approved by the policy hook"
            )

        return ClaudeAgentOptions(
            tools=enabled,
            disallowed_tools=disallowed,
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": task.system_prompt,
            },
            permission_mode="default",
            hooks={
                "PreToolUse": [
                    HookMatcher(
                        matcher=None,
                        hooks=[pre_tool_use],
                        timeout=self.config.hook_timeout_seconds,
                        on_failure="block",
                    )
                ]
            },
            can_use_tool=can_use_tool,
            setting_sources=[],
            strict_mcp_config=True,
            mcp_servers={},
            cwd=str(task.workdir),
            model=task.model,
            max_budget_usd=task.budget.max_usd,
            max_turns=task.budget.max_turns,
            resume=resume,
            # The Claude Code process passes its environment to the commands it runs;
            # PRAT's own settings are blanked so agent commands cannot read them.
            env={**_blank_prat_settings(), **self.config.env, **task.env},
            cli_path=self.config.cli_path,
            stderr=self._stderr.append,
        )

    # Protocol -----------------------------------------------------------------

    def run(self, task: AgentTask) -> AsyncIterator[RuntimeEvent]:
        return self._session(task, resume=None)

    def resume(self, session_id: str, task: AgentTask) -> AsyncIterator[RuntimeEvent]:
        return self._session(task, resume=session_id)

    async def cancel(self, session_id: str) -> None:
        self._cancelled.add(session_id)
        live = next((v for v in self._live.values() if v.session_id == session_id), None)
        if live is None:
            return
        live.cancel.set()
        if live.client is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    live.client.interrupt(), self.config.interrupt_timeout_seconds
                )

    # Internals -----------------------------------------------------------------

    async def _session(self, task: AgentTask, *, resume: str | None) -> AsyncIterator[RuntimeEvent]:
        live = _Live(cancel=asyncio.Event())
        key = uuid.uuid4().hex
        mapper = MessageMapper(resumed=resume is not None, output_limit=self.config.output_limit)
        options = self.build_options(task, resume=resume, denials=mapper.denials)
        client = self._factory(options)
        live.client = client
        self._live[key] = live
        queue: asyncio.Queue[object] = asyncio.Queue()
        pump: asyncio.Task[None] | None = None
        try:
            try:
                await client.connect()
                await client.query(RESUME_PROMPT if resume else task.prompt)
            except Exception as exc:
                yield DoneEvent(Outcome.ERROR, self._error("could not start Claude Code", exc))
                return
            pump = asyncio.create_task(self._pump(client, queue), name=f"prat-claude-{key}")
            while True:
                item = await self._next(queue, live.cancel)
                if item is _CANCELLED:
                    yield DoneEvent(Outcome.CANCELLED, "cancelled")
                    return
                if item is _END:
                    yield DoneEvent(Outcome.ERROR, self._error("session ended without a result"))
                    return
                if isinstance(item, BaseException):
                    yield DoneEvent(Outcome.ERROR, self._error("Claude Code failed", item))
                    return
                for event in mapper.map(item):
                    if isinstance(event, SessionStarted):
                        live.session_id = event.session_id
                        if event.session_id in self._cancelled:
                            live.cancel.set()
                    yield event
                    if isinstance(event, DoneEvent):
                        return
        finally:
            if pump is not None:
                pump.cancel()
                with contextlib.suppress(BaseException):
                    await pump
            with contextlib.suppress(Exception):
                await client.disconnect()
            self._live.pop(key, None)

    @staticmethod
    async def _pump(client: SdkClient, queue: asyncio.Queue[object]) -> None:
        try:
            async for message in client.receive_response():
                await queue.put(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await queue.put(exc)
            return
        await queue.put(_END)

    @staticmethod
    async def _next(queue: asyncio.Queue[object], cancel: asyncio.Event) -> object:
        if cancel.is_set():
            return _CANCELLED
        getter = asyncio.ensure_future(queue.get())
        stopper = asyncio.ensure_future(cancel.wait())
        try:
            done, _ = await asyncio.wait({getter, stopper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in (getter, stopper):
                if not waiter.done():
                    waiter.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await waiter
        if stopper in done:
            return _CANCELLED
        return getter.result()

    def _error(self, what: str, exc: BaseException | None = None) -> str:
        parts = [what]
        if exc is not None:
            parts.append(f"{type(exc).__name__}: {exc}")
        tail = [line.strip() for line in list(self._stderr)[-3:] if line.strip()]
        if tail:
            parts.append("stderr: " + " | ".join(tail))
        return "; ".join(parts)[: self.config.output_limit]


def tools_reported(events: Sequence[RuntimeEvent]) -> tuple[str, ...]:
    """The tool list the agent reported at session start (empty if unknown)."""
    for event in events:
        if isinstance(event, SessionStarted):
            return event.tools
    return ()
