"""Presentation helpers: formatting, diff parsing and journal entry views.

Everything here is pure: it turns stored data into small view objects that
templates render. Text that came from an agent, a tool or a gate is passed
through :func:`redact` so credential-shaped literals are masked on screen.
Escaping is the template engine's job (autoescape is always on).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from prat.domain import CardStatus, StepStatus
from prat.git import DiffStats
from prat.journal import JournalEntry
from prat.policy.secrets import SECRET_PATTERNS

# Cards ---------------------------------------------------------------------------

BOARD_ORDER: tuple[CardStatus, ...] = (
    CardStatus.DRAFT,
    CardStatus.READY,
    CardStatus.RUNNING,
    CardStatus.REVIEW,
    CardStatus.REWORK,
    CardStatus.BLOCKED,
    CardStatus.FAILED,
    CardStatus.DONE,
    CardStatus.CANCELLED,
)

STATUS_LABEL: Mapping[str, str] = {
    "draft": "Draft",
    "ready": "Ready",
    "running": "Running",
    "review": "Review",
    "rework": "Rework",
    "blocked": "Blocked",
    "failed": "Failed",
    "done": "Done",
    "cancelled": "Cancelled",
    # runs and steps
    "queued": "Queued",
    "interrupted": "Interrupted",
    "succeeded": "Succeeded",
    "pending": "Pending",
    "skipped": "Skipped",
}

STATUS_TONE: Mapping[str, str] = {
    "draft": "neutral",
    "ready": "info",
    "running": "accent",
    "review": "ok",
    "rework": "warn",
    "blocked": "warn",
    "failed": "bad",
    "done": "ok",
    "cancelled": "neutral",
    "queued": "info",
    "interrupted": "warn",
    "succeeded": "ok",
    "pending": "neutral",
    "skipped": "neutral",
}

STATUS_HELP: Mapping[str, str] = {
    "draft": "Not ready to run yet.",
    "ready": "Waiting for a run slot.",
    "running": "An agent is working on it.",
    "review": "Passed the gates and the reviewer. Waiting for you.",
    "rework": "Sent back to the builder with a fix list.",
    "blocked": "The agent needs an answer before it can continue.",
    "failed": "The run did not produce acceptable work.",
    "done": "Merged locally into the default branch.",
    "cancelled": "Stopped by request.",
}

VERDICT_LABEL: Mapping[str, str] = {
    "met": "Criteria met",
    "partial": "Partly met",
    "not_met": "Not met",
    "wrong": "Wrong change",
}
VERDICT_TONE: Mapping[str, str] = {
    "met": "ok",
    "partial": "warn",
    "not_met": "bad",
    "wrong": "bad",
}


def status_label(value: object) -> str:
    text = str(value)
    return STATUS_LABEL.get(text, text.replace("_", " ").capitalize())


def status_tone(value: object) -> str:
    return STATUS_TONE.get(str(value), "neutral")


# Formatting ------------------------------------------------------------------------


def fmt_usd(value: float | None) -> str:
    if value is None:
        return "-"
    if value == 0:
        return "$0.00"
    if abs(value) < 0.01:
        return f"${value:.4f}"
    return f"${value:,.2f}"


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def fmt_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def fmt_time(value: datetime | None) -> str:
    value = utc(value)
    return value.strftime("%Y-%m-%d %H:%M:%S UTC") if value else "-"


def fmt_ago(value: datetime | None, now: datetime | None = None) -> str:
    value = utc(value)
    if value is None:
        return "-"
    delta = ((now or datetime.now(UTC)) - value).total_seconds()
    if delta < 45:
        return "just now"
    if delta < 3600:
        return f"{round(delta / 60)} min ago"
    if delta < 86400:
        return f"{round(delta / 3600)} h ago"
    return f"{round(delta / 86400)} d ago"


def seconds_between(start: datetime | None, end: datetime | None) -> float | None:
    start, end = utc(start), utc(end)
    if start is None or end is None:
        return None
    return max((end - start).total_seconds(), 0.0)


def _mask(text: str) -> str:
    keep = 4 if len(text) > 12 else 0
    return text[:keep] + "[redacted]"


def redact(text: str | None) -> str:
    """Mask credential-shaped literals in agent, tool and gate output."""
    if not text:
        return ""
    for pattern in SECRET_PATTERNS:
        text = pattern.regex.sub(lambda m: _mask(m.group(0)), text)
    return text


def criteria_items(text: str | None) -> list[str]:
    """Split acceptance criteria into items. Bullets and numbering start an item."""
    items: list[str] = []
    bullet = re.compile(r"^\s*(?:[-*•]|\d+[.)]|\[[ xX]\])\s+")
    has_bullets = any(bullet.match(line) for line in (text or "").splitlines())
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if not has_bullets or bullet.match(line):
            items.append(bullet.sub("", line).strip())
        elif items:
            items[-1] += " " + line.strip()
        else:
            items.append(line.strip())
    return items


# Diff ---------------------------------------------------------------------------------


@dataclass
class DiffLine:
    kind: str  # add, del, ctx, hunk, note
    text: str
    old: int | None = None
    new: int | None = None


@dataclass
class FileDiff:
    path: str
    status: str
    added: int | None
    deleted: int | None
    size_bytes: int = 0
    lines: list[DiffLine] = field(default_factory=list)
    hidden_lines: int = 0

    @property
    def binary(self) -> bool:
        return self.added is None

    @property
    def status_label(self) -> str:
        return {"A": "added", "D": "deleted", "M": "modified", "T": "type changed"}.get(
            self.status[:1], self.status
        )

    @property
    def anchor(self) -> str:
        return "file-" + re.sub(r"[^a-zA-Z0-9]+", "-", self.path).strip("-").lower()


_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _split_files(text: str) -> dict[str, tuple[list[str], int]]:
    """Map path to (lines, size in bytes) from a ``git diff`` with ``a/`` ``b/`` prefixes."""
    files: dict[str, tuple[list[str], int]] = {}
    current: list[str] | None = None
    path = ""
    size = 0
    for line in text.splitlines():
        if line.startswith("diff --git "):
            if current is not None:
                files[path] = (current, size)
            current, size = [], 0
            match = re.match(r"^diff --git a/(.+) b/(.+)$", line)
            path = match.group(2) if match else line[11:]
        if current is None:
            continue
        size += len(line.encode("utf-8", "replace")) + 1
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target.startswith("b/"):
                path = target[2:]
            elif target == "/dev/null":
                pass
        current.append(line)
    if current is not None:
        files[path] = (current, size)
    return files


def _diff_lines(raw: Sequence[str], limit: int) -> tuple[list[DiffLine], int]:
    out: list[DiffLine] = []
    old = new = 0
    in_hunk = False
    shown = 0
    hidden = 0
    for line in raw:
        hunk = _HUNK.match(line)
        if hunk:
            old, new = int(hunk.group(1)), int(hunk.group(2))
            in_hunk = True
            kind, text, o, n = "hunk", redact(line), None, None
        elif not in_hunk:
            continue  # file headers (index, ---, +++, mode) are summarised elsewhere
        elif line.startswith("+"):
            kind, text, o, n = "add", redact(line[1:]), None, new
            new += 1
        elif line.startswith("-"):
            kind, text, o, n = "del", redact(line[1:]), old, None
            old += 1
        elif line.startswith("\\"):
            kind, text, o, n = "note", line[2:], None, None
        else:
            kind, text, o, n = "ctx", redact(line[1:] if line.startswith(" ") else line), old, new
            old += 1
            new += 1
        if shown >= limit:
            hidden += 1
            continue
        shown += 1
        out.append(DiffLine(kind, text, o, n))
    return out, hidden


def build_diff(stats: DiffStats, text: str, *, max_lines_per_file: int = 1500) -> list[FileDiff]:
    chunks = _split_files(text)
    result: list[FileDiff] = []
    for item in stats.files:
        raw, size = chunks.get(item.path, ([], 0))
        lines, hidden = _diff_lines(raw, max_lines_per_file)
        result.append(
            FileDiff(
                path=item.path,
                status=item.status,
                added=item.added,
                deleted=item.deleted,
                size_bytes=size,
                lines=lines,
                hidden_lines=hidden,
            )
        )
    return result


# Journal ----------------------------------------------------------------------------

CATEGORY_LABEL: Mapping[str, str] = {
    "text": "Agent text",
    "tool": "Tool calls",
    "result": "Tool results",
    "denied": "Denials",
    "policy": "Policy",
    "step": "Steps",
    "verify": "Verification",
    "usage": "Model usage",
    "session": "Sessions",
    "other": "Other",
}
AGENT_LABEL: Mapping[str, str] = {
    "builder": "Builder",
    "planner": "Planner",
    "critic": "Reviewer",
    "engine": "Engine",
}
_ROLE_PREFIX = {"runtime": "builder", "planner": "planner", "critic": "critic"}
LONG_TEXT = 600


@dataclass
class EntryView:
    seq: int
    ts: datetime
    offset: float
    kind: str
    agent: str
    category: str
    tone: str  # neutral, ok, warn, bad, denied, accent
    title: str
    detail: str = ""
    code: str = ""
    # Long output starts folded; the summary says how much is inside.
    fold_code: bool = False
    fold_detail: bool = False

    @property
    def code_lines(self) -> int:
        return self.code.count("\n") + 1 if self.code else 0

    @property
    def detail_lines(self) -> int:
        return self.detail.count("\n") + 1 if self.detail else 0

    @property
    def agent_label(self) -> str:
        return AGENT_LABEL.get(self.agent, self.agent)

    @property
    def category_label(self) -> str:
        return CATEGORY_LABEL.get(self.category, self.category)

    @property
    def offset_label(self) -> str:
        minutes, seconds = divmod(self.offset, 60)
        return f"+{int(minutes):02d}:{seconds:04.1f}"


def _short_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


def _call_target(data: Mapping[str, Any]) -> str:
    for key in ("command", "file_path", "path", "pattern", "url", "tool_name"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def present(entry: JournalEntry, start: datetime | None = None) -> EntryView:
    data = entry.data
    kind = entry.kind
    prefix, _, event = kind.partition(".")
    agent = _ROLE_PREFIX.get(prefix, "engine")
    offset = seconds_between(start, entry.ts) or 0.0
    view = EntryView(entry.seq, entry.ts, offset, kind, agent, "other", "neutral", kind)

    if kind == "policy":
        view.agent = str(data.get("agent") or "builder")
        raw_call = data.get("call")
        call: Mapping[str, Any] = raw_call if isinstance(raw_call, Mapping) else {}
        target = redact(_call_target(call))
        what = str(call.get("kind", "call"))
        if data.get("allowed"):
            view.category, view.tone = "policy", "neutral"
            view.title = f"Allowed {what}"
            view.detail = target
        else:
            view.category, view.tone = "denied", "denied"
            view.title = f"Denied {what}" + (" (loop detector)" if data.get("loop") else "")
            view.code = target
            view.detail = redact(str(data.get("reason") or ""))
        return view
    if prefix in _ROLE_PREFIX:
        if event == "text":
            view.category, view.title = "text", "Message"
            view.detail = redact(str(data.get("text") or ""))
            view.fold_detail = len(view.detail) > LONG_TEXT * 3
        elif event == "tool_call":
            raw_input = data.get("input")
            tool_input: Mapping[str, Any] = raw_input if isinstance(raw_input, Mapping) else {}
            view.category, view.tone = "tool", "accent"
            view.title = str(data.get("tool") or "tool")
            target = _call_target(tool_input)
            rest = {k: v for k, v in tool_input.items() if v != target}
            view.code = redact(target or _short_json(dict(tool_input)))
            if target and rest:
                view.detail = redact(_short_json(rest))
                view.fold_detail = True
        elif event == "tool_result":
            view.category = "result"
            if data.get("denied"):
                # The policy entry just before this one carries the reason.
                view.title = "Not run: denied by policy"
            else:
                ok = bool(data.get("ok"))
                view.tone = "neutral" if ok else "bad"
                view.title = "Result" if ok else "Result (error)"
                view.code = redact(str(data.get("output") or ""))
                view.fold_code = len(view.code) > LONG_TEXT or view.code.count("\n") > 12
        elif event == "cost":
            view.category = "usage"
            tokens_in, tokens_out = data.get("input_tokens"), data.get("output_tokens")
            view.title = "Model usage"
            if tokens_in or tokens_out:
                view.title = f"Model usage: {tokens_in or 0:,} in, {tokens_out or 0:,} out tokens"
        elif event == "session":
            view.category = "session"
            view.title = "Session resumed" if data.get("resumed") else "Session started"
            tools = data.get("tools") or []
            parts = []
            if data.get("model"):
                parts.append(f"model {data['model']}")
            if tools:
                parts.append("tools: " + ", ".join(str(t) for t in tools))
            view.detail = "; ".join(parts)
        elif event == "blocked":
            view.category, view.tone = "step", "warn"
            view.title = "Agent is blocked"
            view.detail = redact(str(data.get("reason") or ""))
        elif event == "done":
            outcome = str(data.get("outcome") or "")
            view.category = "step"
            view.tone = "ok" if outcome == "completed" else "warn"
            view.title = f"Session ended: {outcome.replace('_', ' ')}"
            view.detail = redact(str(data.get("message") or ""))
        else:
            view.detail = redact(_short_json(dict(data)))
        return view
    if kind in ("run.started", "run.resumed"):
        view.category, view.tone = "step", "accent"
        view.title = "Run started" if kind == "run.started" else "Run resumed"
    elif kind == "step.started":
        view.category = "step"
        view.title = f"{str(data.get('step', '')).capitalize()} started"
    elif kind == "step.finished":
        status = str(data.get("status") or "")
        view.category = "step"
        view.tone = {"succeeded": "ok", "failed": "bad", "skipped": "neutral"}.get(
            status, "neutral"
        )
        view.title = f"{str(data.get('step', '')).capitalize()} {status}"
        view.detail = redact(str(data.get("reason") or data.get("error") or ""))
    elif kind == "execute.model":
        view.category = "session"
        view.title = f"Builder model {data.get('model', '')}"
        tools = data.get("tools") or []
        view.detail = "tools: " + ", ".join(str(t) for t in tools) if tools else ""
        files = data.get("repo_instructions") or []
        if files:
            shown = "repository instructions: " + ", ".join(str(f) for f in files)
            view.detail = f"{view.detail}; {shown}" if view.detail else shown
    elif kind == "verify.finding":
        view.category, view.tone = "verify", "bad"
        view.title = f"Finding: {data.get('kind', '')}"
        view.detail = redact(str(data.get("message") or ""))
    elif kind == "verify.gate":
        passed = bool(data.get("passed"))
        view.category, view.tone = "verify", "ok" if passed else "bad"
        name = data.get("name", "")
        view.title = f"Gate {name} {'passed' if passed else 'failed'}"
        duration = data.get("duration_seconds")
        view.detail = f"{data.get('command', '')} ({fmt_duration(float(duration or 0))})"
        view.code = redact(str(data.get("output_tail") or ""))
        view.fold_code = bool(view.code)
    else:
        view.detail = redact(_short_json(dict(data)))
    return view


def present_all(entries: Iterable[JournalEntry]) -> list[EntryView]:
    items = list(entries)
    start = items[0].ts if items else None
    return [present(entry, start) for entry in items]


@dataclass(frozen=True)
class ToolCount:
    tool: str
    calls: int
    denied: int
    percent: float


def tool_histogram(entries: Sequence[JournalEntry]) -> list[ToolCount]:
    calls: Counter[str] = Counter()
    denied: Counter[str] = Counter()
    by_call: dict[str, str] = {}
    for entry in entries:
        prefix, _, event = entry.kind.partition(".")
        if prefix not in _ROLE_PREFIX:
            continue
        if event == "tool_call":
            tool = str(entry.data.get("tool") or "tool")
            calls[tool] += 1
            by_call[str(entry.data.get("call_id"))] = tool
        elif event == "tool_result" and entry.data.get("denied"):
            denied[by_call.get(str(entry.data.get("call_id")), "tool")] += 1
    top = max(calls.values(), default=0)
    return [
        ToolCount(tool, count, denied[tool], round(100 * count / top, 1) if top else 0.0)
        for tool, count in sorted(calls.items(), key=lambda item: (-item[1], item[0]))
    ]


def category_counts(views: Sequence[EntryView]) -> dict[str, int]:
    counts: Counter[str] = Counter(v.category for v in views)
    return {key: counts[key] for key in CATEGORY_LABEL if counts[key]}


# Steps ---------------------------------------------------------------------------------


@dataclass
class StepView:
    name: str
    status: str
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    seconds: float | None
    error: str
    facts: list[tuple[str, str]]
    percent: float = 0.0

    @property
    def label(self) -> str:
        return self.name.capitalize()

    @property
    def running(self) -> bool:
        return self.status == StepStatus.RUNNING


def step_facts(name: str, detail: Mapping[str, Any]) -> list[tuple[str, str]]:
    facts: list[tuple[str, str]] = []
    if detail.get("model"):
        facts.append(("model", str(detail["model"])))
    if name == "execute" and detail.get("files_changed") is not None:
        facts.append(("files", str(detail["files_changed"])))
    if name == "critique" and detail.get("verdict"):
        facts.append(("verdict", VERDICT_LABEL.get(str(detail["verdict"]), str(detail["verdict"]))))
    if name == "verify" and detail.get("gates"):
        gates = detail["gates"]
        passed = sum(1 for g in gates if g.get("passed"))
        facts.append(("gates", f"{passed}/{len(gates)} passed"))
    if detail.get("reason") and name != "execute":
        facts.append(("note", redact(str(detail["reason"]))))
    return facts
