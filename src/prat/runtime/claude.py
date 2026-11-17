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
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    PermissionResult,
    PermissionResultDeny,
    RateLimitEvent,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TextBlock,
    ThinkingBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

from prat.model_registry import context_window, estimate_cost
from prat.policy import Decision, ToolCall
from prat.runtime.base import (
    TOOL_BASH,
    TOOL_EDIT,
    TOOL_GLOB,
    TOOL_GREP,
    TOOL_HELPERS,
    TOOL_MCP,
    TOOL_READ,
    TOOL_SKILLS,
    TOOL_TASKS,
    TOOL_WEB_FETCH,
    TOOL_WEB_SEARCH,
    TOOL_WRITE,
    AgentTask,
    BlockedEvent,
    CompactEvent,
    ContextEvent,
    CostEvent,
    DoneEvent,
    HelperEvent,
    Outcome,
    PratTool,
    PressureEvent,
    RuntimeEvent,
    SessionStarted,
    SkillEvent,
    TaskItem,
    TasksEvent,
    TextEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageEvent,
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
    # The crew: the agent's task list, its helpers and its skills.
    TOOL_TASKS: ("TodoWrite", "TaskCreate", "TaskUpdate", "TaskGet", "TaskList"),
    TOOL_HELPERS: ("Agent", "Task"),
    TOOL_SKILLS: ("Skill",),
}
# PRAT's own in-process tools: offered when a task carries them, always allowed (they only
# record what the agent tells PRAT, or read through PRAT's own guarded code).
PRAT_SERVER = "prat"
PRAT_TOOL_PREFIX = f"mcp__{PRAT_SERVER}__"
HELPER_TOOLS = frozenset({"Agent", "Task"})
TASK_TOOLS = frozenset({"TodoWrite", "TaskCreate", "TaskUpdate", "TaskGet", "TaskList"})
# At most this many helpers at once per session, and helpers cannot call helpers.
MAX_CONCURRENT_HELPERS = 3
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
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
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
# Claude Code settings for every agent session: no attribution in commits or pull requests.
AGENT_SETTINGS = json.dumps({"includeCoAuthoredBy": False, "attribution": {"commit": "", "pr": ""}})
# The SDK passes the appended system prompt on Claude Code's command line, and Windows caps a
# command line near 32,000 characters; past it the process cannot start at all (the SDK then
# reports "Claude Code not found"). Long instructions go in the opening message instead, which
# travels over the session's input with no such limit.
WINDOWS_SYSTEM_PROMPT_LIMIT = 12_000
MOVED_INSTRUCTIONS_NOTE = (
    "Your full instructions for this task are at the top of the first message, between "
    "<instructions> and </instructions>. Follow them as your system instructions."
)


def fit_instructions(
    system_prompt: str, opening: str, *, windows: bool | None = None
) -> tuple[str, str]:
    """(system prompt, opening message) that Claude Code can be started with on this machine."""
    if windows is None:
        windows = os.name == "nt"
    if not windows or len(system_prompt) <= WINDOWS_SYSTEM_PROMPT_LIMIT:
        return system_prompt, opening
    return MOVED_INSTRUCTIONS_NOTE, f"<instructions>\n{system_prompt}\n</instructions>\n\n{opening}"


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
    if tool_name.startswith(PRAT_TOOL_PREFIX) or tool_name in TASK_TOOLS | HELPER_TOOLS:
        # Nothing to judge: a task list and PRAT's own tools act on nothing, and every tool
        # call a helper makes comes through this hook on its own.
        return []
    if tool_name == "Skill":
        return []
    if tool_name.startswith("mcp__"):
        return [ToolCall.mcp(tool_name, dict(tool_input))]
    return Decision.deny(f"tool {tool_name} is not available to PRAT agents")


def sandboxed_input(
    tool_name: str, tool_input: Any, shell: Callable[[str], str] | None
) -> dict[str, Any] | None:
    """The Bash tool's input with the command moved into the sandbox, or ``None`` when there
    is no sandbox. Every command moves, git included: with a sandbox, nothing the agent writes
    runs on this machine (prat.sandbox)."""
    if shell is None or tool_name != "Bash" or not isinstance(tool_input, Mapping):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    return {**tool_input, "command": shell(command)}


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
            if tool_name.startswith(PRAT_TOOL_PREFIX):
                pass
            elif tool_name.startswith("mcp__"):
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


# The crew ----------------------------------------------------------------------


def crew_options(task: AgentTask, enabled: Sequence[str]) -> dict[str, Any]:
    """Helpers (subagents) and the skill loadout for a session, when its tools include them."""
    options: dict[str, Any] = {}
    if TOOL_HELPERS in task.allowed_tools and task.helpers:
        options["agents"] = {
            name: AgentDefinition(
                description=spec.description,
                prompt=spec.prompt,
                # A helper never gets helpers of its own, nor anything its lead does not have.
                tools=[
                    t
                    for t in sdk_tool_names(spec.tools & task.allowed_tools - {TOOL_HELPERS})
                    if t in enabled
                ]
                + [
                    f"{PRAT_TOOL_PREFIX}{t.name}"
                    for t in task.prat_tools
                    if t.name in READ_ONLY_PRAT_TOOLS
                ],
                model=spec.model or "inherit",
            )
            for name, spec in task.helpers.items()
        }
    if TOOL_SKILLS in task.allowed_tools and task.skill_dirs:
        options["plugins"] = [{"type": "local", "path": str(path)} for path in task.skill_dirs]
        # Exactly the loadout's skills, by name: never the machine's own (~/.claude) skills.
        options["skills"] = loadout_skill_names(task.skill_dirs)
    if task.thinking:
        # The model decides how much to think; PRAT keeps a summary of each thought.
        options["thinking"] = {"type": "adaptive", "display": "summarized"}
    return options


def loadout_skill_names(skill_dirs: Sequence[Path]) -> list[str]:
    """Each plugin folder's skills as ``<plugin>:<skill>``, the names the SDK loads them by."""
    names: list[str] = []
    for root in skill_dirs:
        try:
            plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text("utf-8"))
        except (OSError, ValueError):
            continue
        prefix = str(plugin.get("name") or root.name)
        skills_root = root / "skills"
        if skills_root.is_dir():
            names += [
                f"{prefix}:{p.name}"
                for p in sorted(skills_root.iterdir())
                if (p / "SKILL.md").is_file()
            ]
    return names


# PRAT tools a helper may use too: they only read.
READ_ONLY_PRAT_TOOLS = frozenset({"query_database", "job_status"})


def crew_env(task: AgentTask) -> dict[str, str]:
    env: dict[str, str] = {}
    if TOOL_TASKS in task.allowed_tools:
        env["CLAUDE_CODE_ENABLE_TODO_TOOLS"] = "1"
    if TOOL_HELPERS in task.allowed_tools:
        env["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] = str(MAX_CONCURRENT_HELPERS)
        env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] = "1"
    return env


def prat_server(tools: Sequence[PratTool]) -> dict[str, Any]:
    """PRAT's own tools as an in-process MCP server named ``prat``."""
    if not tools:
        return {}

    def wrap(item: PratTool) -> Any:
        async def run(args: dict[str, Any]) -> dict[str, Any]:
            try:
                text = await item.handler(args)
            except Exception as exc:  # the agent reads the problem; the session goes on
                return {"content": [{"type": "text", "text": f"failed: {exc}"}], "is_error": True}
            return {"content": [{"type": "text", "text": text}]}

        return tool(item.name, item.description, dict(item.schema))(run)

    return {PRAT_SERVER: create_sdk_mcp_server(name=PRAT_SERVER, tools=[wrap(t) for t in tools])}


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


_TASK_ID = re.compile(r"#\s*(\d+)")
# The answer of a helper that runs in the background; its real answer comes as a notification.
_LAUNCHED = re.compile(
    r"async_launched|async agent launched|running in the background|launched in the background",
    re.I,
)


def _int(mapping: Mapping[str, Any] | None, key: str) -> int:
    value = (mapping or {}).get(key)
    return int(value) if isinstance(value, int | float) and value > 0 else 0


@dataclass
class MessageMapper:
    """Turns SDK messages into PRAT runtime events. One per session.

    Besides text, tool calls and cost, it follows the crew: helpers (subagents) starting and
    finishing, skills loaded, the agent's task list, how full the lead's context is, when the
    session compacts, rate limit pressure, and the session's own per-model usage at the end.
    """

    resumed: bool = False
    output_limit: int = 4000
    denials: dict[str, str] = field(default_factory=dict)
    session_id: str | None = None
    estimated_usd: float = 0.0
    last_text: str = ""
    _seen_messages: set[str] = field(default_factory=set)
    # Helper call id -> helper name; helpers still running.
    _helpers: dict[str, str] = field(default_factory=dict)
    _running_helpers: set[str] = field(default_factory=set)
    # Task id -> (subject, status), in creation order; task-create calls awaiting their id.
    _tasks: dict[str, TaskItem] = field(default_factory=dict)
    _creating: dict[str, str] = field(default_factory=dict)

    def map(self, message: Any) -> list[RuntimeEvent]:
        if isinstance(message, TaskNotificationMessage):
            return self._helper_done(message.tool_use_id, message.summary or "", ok=True)
        if isinstance(message, SystemMessage):
            return self._system(message)
        if isinstance(message, RateLimitEvent):
            return self._pressure(message)
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
        data = message.data
        if message.subtype == "compact_boundary":
            meta = data.get("compact_metadata") if isinstance(data, Mapping) else None
            meta = meta if isinstance(meta, Mapping) else {}
            return [CompactEvent(_int(meta, "pre_tokens"), str(meta.get("trigger") or ""))]
        if message.subtype != "init":
            return []
        tools = data.get("tools")
        return self._session(
            data.get("session_id"),
            tools=tuple(str(t) for t in tools) if isinstance(tools, list) else (),
            model=str(data.get("model") or ""),
        )

    @staticmethod
    def _pressure(message: RateLimitEvent) -> list[RuntimeEvent]:
        info = message.rate_limit_info
        resets = getattr(info, "resets_at", None)
        utilization = getattr(info, "utilization", None)
        return [
            PressureEvent(
                status=str(getattr(info, "status", "") or ""),
                utilization=float(utilization) if utilization is not None else None,
                resets_at=float(resets) if isinstance(resets, int | float) else None,
                kind=str(getattr(info, "rate_limit_type", "") or ""),
            )
        ]

    def _agent(self, parent: str | None) -> str:
        if not parent:
            return "lead"
        return self._helpers.get(parent, "helper")

    def _assistant(self, message: AssistantMessage) -> list[RuntimeEvent]:
        events: list[RuntimeEvent] = list(self._session(message.session_id))
        agent = self._agent(message.parent_tool_use_id)
        lead = agent == "lead"
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                if lead:
                    self.last_text = block.text
                    events.append(TextEvent(block.text))
            elif isinstance(block, ThinkingBlock):
                if lead and block.thinking.strip():
                    events.append(ThoughtEvent(block.thinking.strip()[: self.output_limit]))
            elif isinstance(block, ToolUseBlock):
                tool_input = dict(block.input)
                if lead:
                    events.append(ToolCallEvent(block.id, block.name, tool_input))
                events.extend(self._crew_call(block.id, block.name, tool_input, agent))
        key = message.message_id or message.uuid
        if message.usage and (key is None or key not in self._seen_messages):
            if key is not None:
                self._seen_messages.add(key)
            usage = message.usage
            usd = estimate_cost(message.model, usage)
            if usd > 0:
                self.estimated_usd += usd
                events.append(
                    CostEvent(
                        usd=usd,
                        input_tokens=_int(usage, "input_tokens"),
                        output_tokens=_int(usage, "output_tokens"),
                        model=message.model or "",
                        cache_read_tokens=_int(usage, "cache_read_input_tokens"),
                        cache_write_tokens=_int(usage, "cache_creation_input_tokens"),
                        agent=agent,
                    )
                )
            if lead:
                held = (
                    _int(usage, "input_tokens")
                    + _int(usage, "cache_read_input_tokens")
                    + _int(usage, "cache_creation_input_tokens")
                    + _int(usage, "output_tokens")
                )
                if held:
                    events.append(ContextEvent(held, context_window(message.model)))
        return events

    def _crew_call(
        self, call_id: str, name: str, tool_input: Mapping[str, Any], agent: str
    ) -> list[RuntimeEvent]:
        """Helpers starting, skills loading and the task list changing."""
        if name in HELPER_TOOLS:
            helper = str(tool_input.get("subagent_type") or "helper")
            self._helpers[call_id] = helper
            self._running_helpers.add(call_id)
            what = str(tool_input.get("description") or tool_input.get("prompt") or "")
            return [HelperEvent(call_id, helper, "started", prompt=what[:300])]
        if name == "Skill":
            skill = str(tool_input.get("skill") or tool_input.get("name") or "")
            return [SkillEvent(skill.split(":")[-1], agent)] if skill else []
        if agent != "lead":
            return []
        if name == "TodoWrite":
            todos = tool_input.get("todos")
            items = [
                TaskItem(str(t.get("content") or t.get("subject") or ""), str(t.get("status")))
                for t in todos or []
                if isinstance(t, Mapping)
            ]
            self._tasks = {str(i): item for i, item in enumerate(items)}
            return [TasksEvent(tuple(items))]
        if name == "TaskCreate":
            self._creating[call_id] = str(
                tool_input.get("subject") or tool_input.get("description") or ""
            )
            return []
        if name == "TaskUpdate":
            task_id = str(tool_input.get("taskId") or tool_input.get("task_id") or "")
            current = self._tasks.get(task_id)
            if current is None:
                return []
            status = str(tool_input.get("status") or current.status)
            if status == "deleted":
                self._tasks.pop(task_id, None)
            else:
                subject = str(tool_input.get("subject") or current.subject)
                self._tasks[task_id] = TaskItem(subject, status)
            return [TasksEvent(tuple(self._tasks.values()))]
        return []

    def _helper_done(self, call_id: str | None, summary: str, *, ok: bool) -> list[RuntimeEvent]:
        if not call_id or call_id not in self._running_helpers:
            return []
        self._running_helpers.discard(call_id)
        name = self._helpers.get(call_id, "helper")
        return [HelperEvent(call_id, name, "finished", summary=summary[:500], ok=ok)]

    def _user(self, message: UserMessage) -> list[RuntimeEvent]:
        if isinstance(message.content, str):
            return []
        events: list[RuntimeEvent] = []
        lead = self._agent(getattr(message, "parent_tool_use_id", None)) == "lead"
        for block in message.content:
            if not isinstance(block, ToolResultBlock):
                continue
            call_id = block.tool_use_id
            reason = self.denials.pop(call_id, None)
            if reason is not None:
                if lead:
                    events.append(ToolResultEvent(call_id, ok=False, denied=True, reason=reason))
                events.extend(self._helper_done(call_id, reason, ok=False))
                continue
            text = _result_text(block.content, self.output_limit)
            if call_id in self._creating:
                found = _TASK_ID.search(text)
                subject = self._creating.pop(call_id)
                if found and not block.is_error:
                    self._tasks[found.group(1)] = TaskItem(subject, "pending")
                    events.append(TasksEvent(tuple(self._tasks.values())))
            if call_id in self._running_helpers and not _LAUNCHED.search(text):
                events.extend(self._helper_done(call_id, text, ok=not block.is_error))
            if lead:
                events.append(ToolResultEvent(call_id, ok=not block.is_error, output=text))
        return events

    def _result(self, message: ResultMessage) -> list[RuntimeEvent]:
        events: list[RuntimeEvent] = list(self._session(message.session_id))
        if message.total_cost_usd is not None:
            delta = float(message.total_cost_usd) - self.estimated_usd
            if abs(delta) > 1e-9:
                # Reconcile the running estimate with the session's own total.
                self.estimated_usd += delta
                events.append(CostEvent(usd=delta))
        if message.model_usage:
            models = {
                str(model): {
                    "input": float(_int(row, "inputTokens")),
                    "output": float(_int(row, "outputTokens")),
                    "cache_read": float(_int(row, "cacheReadInputTokens")),
                    "cache_write": float(_int(row, "cacheCreationInputTokens")),
                    "usd": float(row.get("costUSD") or 0.0),
                }
                for model, row in message.model_usage.items()
                if isinstance(row, Mapping)
            }
            events.append(
                UsageEvent(
                    models, turns=message.num_turns or 0, duration_ms=message.duration_ms or 0
                )
            )
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
                allowed: dict[str, Any] = {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": decision.reason,
                }
                rewritten = sandboxed_input(name, data.get("tool_input"), task.shell)
                if rewritten is not None:
                    allowed["updatedInput"] = rewritten
                return cast(HookJSONOutput, {"hookSpecificOutput": allowed})
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

        crew = crew_options(task, enabled)
        return ClaudeAgentOptions(
            **crew,
            tools=enabled,
            disallowed_tools=disallowed,
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": fit_instructions(task.system_prompt, "")[0],
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
            # Agents commit as PRAT's agent; Claude Code's own trailers ("Co-Authored-By",
            # "Generated with") would otherwise end up in the project's history.
            settings=AGENT_SETTINGS,
            strict_mcp_config=True,
            mcp_servers=prat_server(task.prat_tools),
            cwd=str(task.workdir),
            model=task.model,
            max_budget_usd=task.budget.max_usd,
            max_turns=task.budget.max_turns,
            resume=resume,
            # The Claude Code process passes its environment to the commands it runs;
            # PRAT's own settings are blanked so agent commands cannot read them.
            env={**_blank_prat_settings(), **self.config.env, **crew_env(task), **task.env},
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
                opening = RESUME_PROMPT if resume else task.prompt
                await client.query(fit_instructions(task.system_prompt, opening)[1])
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
