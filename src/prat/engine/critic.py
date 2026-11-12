"""The agent-backed reviewer (the critic).

It runs in a fresh session that can only read (Read, Glob and Grep, judged
by a read-only policy), sees the card, the diff, the gate results and the
summary computed from the diff, and answers with a JSON verdict. Parsing is
tolerant: a fenced JSON block, then any JSON object with a ``verdict`` key,
then a ``verdict: <word>`` line. An answer that cannot be read becomes
``partial`` so a person looks at the card; a broken reviewer never approves
anything and never sends work back on its own.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any

from prat.domain import Autonomy, Verdict
from prat.engine.instructions import load_repo_instructions
from prat.engine.interfaces import CriterionFinding, ReviewRequest, ReviewResult
from prat.engine.prompts import critic_system_prompt, critic_task_prompt
from prat.journal import read_journal
from prat.policy import Policy
from prat.runtime.base import READ_TOOLS, AgentRuntime, AgentTask, Budget, Outcome
from prat.runtime.session import collect

DEFAULT_DIFF_LIMIT = 60_000
MAX_COMMANDS = 15
MAX_COMMAND_OUTPUT = 1500


def executed_commands(journal_path: str | None) -> list[dict[str, str]]:
    """The shell commands the builder ran in this run and their output, from PRAT's own
    journal (secrets are redacted there), most recent last. Denied calls are left out."""
    if not journal_path:
        return []
    try:
        entries = read_journal(Path(journal_path))
    except OSError:
        return []
    calls: dict[str, str] = {}
    found: list[dict[str, str]] = []
    for entry in entries:
        data = entry.data or {}
        call_id = str(data.get("call_id", ""))
        if entry.kind == "runtime.tool_call" and str(data.get("tool", "")).lower() == "bash":
            calls[call_id] = str((data.get("input") or {}).get("command", ""))
        elif entry.kind == "runtime.tool_result" and call_id in calls and not data.get("denied"):
            output = str(data.get("output", ""))
            if len(output) > MAX_COMMAND_OUTPUT:
                head, tail = output[: MAX_COMMAND_OUTPUT // 3], output[-MAX_COMMAND_OUTPUT // 2 :]
                output = f"{head}\n[...]\n{tail}"
            found.append({"command": calls[call_id][:600], "output": output})
    return found[-MAX_COMMANDS:]


_VERDICT_ALIASES = {
    "met": Verdict.MET,
    "complete": Verdict.MET,
    "completed": Verdict.MET,
    "pass": Verdict.MET,
    "passed": Verdict.MET,
    "approve": Verdict.MET,
    "approved": Verdict.MET,
    "partial": Verdict.PARTIAL,
    "partially_met": Verdict.PARTIAL,
    "needs_human_eyes": Verdict.PARTIAL,
    "not_met": Verdict.NOT_MET,
    "notmet": Verdict.NOT_MET,
    "unmet": Verdict.NOT_MET,
    "fail": Verdict.NOT_MET,
    "failed": Verdict.NOT_MET,
    "incomplete": Verdict.NOT_MET,
    "wrong": Verdict.WRONG,
    "off_target": Verdict.WRONG,
}
_STATUS_ALIASES = {
    "met": "met",
    "complete": "met",
    "pass": "met",
    "yes": "met",
    "partial": "partial",
    "partially_met": "partial",
    "not_met": "not_met",
    "notmet": "not_met",
    "unmet": "not_met",
    "no": "not_met",
    "fail": "not_met",
}
_FENCED_JSON = re.compile(r"```(?:json|JSON)?\s*\n(.*?)```", re.DOTALL)
_VERDICT_WORD = re.compile(
    r"verdict\W{0,5}(met|partial|not[\s_-]?met|wrong|complete)\b", re.IGNORECASE
)
UNREADABLE = "The reviewer's answer could not be read. A person should review this card."


def _norm(word: Any) -> str:
    return re.sub(r"[\s-]+", "_", str(word).strip().lower())


def normalise_verdict(word: Any) -> Verdict | None:
    return _VERDICT_ALIASES.get(_norm(word))


def _json_objects(text: str) -> list[dict[str, Any]]:
    """Every JSON object found in ``text``: fenced blocks first, then bare objects."""
    found: list[dict[str, Any]] = []
    for block in _FENCED_JSON.findall(text):
        try:
            value = json.loads(block.strip())
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)
    if found:
        return found
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


def _findings(raw: Any) -> list[CriterionFinding]:
    if not isinstance(raw, list):
        return []
    findings = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        criterion = str(item.get("criterion") or item.get("name") or "").strip()
        status = _STATUS_ALIASES.get(_norm(item.get("status", "")), "partial")
        evidence = str(item.get("evidence") or item.get("note") or "").strip()
        if criterion:
            findings.append(CriterionFinding(criterion, status, evidence))
    return findings


def _fix_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [line.lstrip("-* ").strip() for line in raw.splitlines()]
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _confidence(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    value = float(raw)
    if 1 < value <= 100:
        value /= 100
    return value if 0 <= value <= 1 else None


def _from_object(data: dict[str, Any]) -> ReviewResult | None:
    verdict = normalise_verdict(data.get("verdict", ""))
    if verdict is None:
        return None
    findings = _findings(data.get("criteria") or data.get("findings"))
    fixes = _fix_list(data.get("fix_list") or data.get("fixes"))
    concerns: list[str] = []
    if verdict is Verdict.MET and any(f.status == "not_met" for f in findings):
        # A "met" verdict that contradicts its own findings is not trusted.
        verdict = Verdict.PARTIAL
        concerns.append("The reviewer said met but marked a criterion not met.")
    if verdict is Verdict.MET:
        fixes = []
    summary = str(data.get("summary") or "").strip() or f"Reviewer verdict: {verdict}."
    return ReviewResult(
        verdict,
        summary,
        concerns,
        confidence=_confidence(data.get("confidence")),
        findings=findings,
        fix_list=fixes,
    )


def parse_review(text: str) -> ReviewResult:
    """Read a reviewer's answer. Never raises."""
    for data in reversed(_json_objects(text or "")):
        result = _from_object(data)
        if result is not None:
            return result
    words = _VERDICT_WORD.findall(text or "")
    if words:
        verdict = normalise_verdict(words[-1]) or Verdict.PARTIAL
        tail = (text or "").strip()[-600:]
        return ReviewResult(
            verdict,
            tail or f"Reviewer verdict: {verdict}.",
            ["The verdict was read from text; the reviewer gave no JSON."],
        )
    snippet = (text or "").strip()[:500]
    return ReviewResult(Verdict.PARTIAL, UNREADABLE, [snippet] if snippet else [], parsed=False)


class AgentReviewer:
    """A :class:`prat.engine.interfaces.Reviewer` backed by an agent runtime."""

    def __init__(self, runtime: AgentRuntime, *, diff_limit: int = DEFAULT_DIFF_LIMIT) -> None:
        self.runtime = runtime
        self.diff_limit = diff_limit

    def task_for(self, request: ReviewRequest) -> AgentTask:
        workdir = Path(request.workdir) if request.workdir else None
        policy = request.policy
        if policy is None:
            policy = Policy(Autonomy.READ_ONLY, workdir or Path.cwd())
        return AgentTask(
            prompt=critic_task_prompt(
                title=request.title,
                goal=request.goal,
                acceptance_criteria=request.acceptance_criteria,
                summary=request.summary,
                gates=request.gates,
                diff=request.diff,
                diff_limit=self.diff_limit,
                feedback=request.feedback,
                changes=request.changes,
                databases=request.databases,
                knowledge=request.knowledge,
                commands=executed_commands(request.journal_path),
            ),
            # Without a worktree there is no repository to take instruction files from.
            system_prompt=critic_system_prompt(load_repo_instructions(workdir) if workdir else ()),
            workdir=workdir or Path.cwd(),
            # Read, Glob and Grep only; nothing at all without a worktree to read.
            allowed_tools=READ_TOOLS if workdir else frozenset(),
            policy=policy,
            budget=Budget(max_usd=request.budget_usd),
            model=request.model,
            metadata={"role": "critic", "card_id": str(request.card_id)},
        )

    async def review(self, request: ReviewRequest) -> ReviewResult:
        if not request.diff.strip():
            return ReviewResult(
                Verdict.WRONG,
                "The change is empty.",
                fix_list=["Make the change the card asks for."],
            )
        out = await collect(self.runtime, self.task_for(request), on_event=request.on_event)
        if out.outcome is not Outcome.COMPLETED:
            what = f"{out.outcome or 'no result'}: {out.message}".strip()
            return ReviewResult(
                Verdict.PARTIAL,
                f"The reviewer did not finish ({what}). A person should review this card.",
                cost_usd=out.cost_usd,
                parsed=False,
            )
        return dataclasses.replace(parse_review(out.final_text), cost_usd=out.cost_usd)
