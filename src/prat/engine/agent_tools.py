"""PRAT's own tools, offered to an agent in its session (an in-process MCP server ``prat``).

They act only through PRAT's own code, so the policy allows them whatever the autonomy:

* ``remember`` / ``recall`` / ``reflect``: the agent's own memory (prat.memory): notes,
  thoughts and feelings while it works, a search of everything it has ever done, and its
  reflection on this piece of work, which becomes the episode it remembers.
* ``report_progress``: a phase with done and total, shown live in mission control.
* ``note_risk``: something the reviewer and the person should look at.
* ``propose_skill``: a reusable skill, kept as a candidate until a person promotes it.
* ``query_database``: read-only SQL on the card's own branch of a project database.
* ``start_job`` / ``job_status``: the project's long-running jobs (prat.jobs).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prat import crew
from prat.memory import AgentMemory, Feeling
from prat.runtime.base import PratTool

READ_ONLY_SQL = re.compile(r"^\s*(select|with|show|explain|describe|desc|values|table)\b", re.I)
ROW_LIMIT = 200


@dataclass
class Reflection:
    what: str = ""
    reasoning: str = ""
    lesson: str = ""
    feeling: Feeling = field(default_factory=Feeling)
    confidence: float | None = None


@dataclass
class ToolContext:
    home: Path
    project: str
    card_id: int
    run_id: int
    agent: str
    # Where tool calls are recorded: journal entry kind and data.
    record: Callable[[str, dict[str, Any]], Any]
    # Live values for mission control (progress, risks).
    live: Callable[[str, Any], None]
    # Project database URLs on the card's branch, by database name.
    databases: Mapping[str, str] = field(default_factory=dict)
    start_job: Callable[[str], Awaitable[str]] | None = None
    job_status: Callable[[int | None], Awaitable[str]] | None = None
    reflection: Reflection = field(default_factory=Reflection)
    risks: list[str] = field(default_factory=list)


def _feeling(args: Mapping[str, Any]) -> Feeling | None:
    words = str(args.get("feeling") or "").strip()
    if not words:
        return None
    try:
        valence = max(-1.0, min(1.0, float(args.get("valence") or 0.0)))
        intensity = max(0.0, min(1.0, float(args.get("intensity") or 0.5)))
    except (TypeError, ValueError):
        valence, intensity = 0.0, 0.5
    return Feeling(words[:60], valence, intensity)


def build(ctx: ToolContext) -> tuple[PratTool, ...]:
    memory = AgentMemory(ctx.home, ctx.agent)

    async def remember(args: Mapping[str, Any]) -> str:
        kind = str(args.get("kind") or "note").lower()
        if kind not in ("note", "thought", "feeling", "plan", "lesson"):
            kind = "note"
        text = str(args.get("text") or "")
        feeling = _feeling(args)
        await asyncio.to_thread(memory.note, ctx.project, ctx.card_id, kind, text, feeling)
        ctx.record("memory.remember", {"kind": kind, "text": text[:600]})
        return "Kept in your working memory of this card."

    async def recall(args: Mapping[str, Any]) -> str:
        query = str(args.get("query") or "")
        found = await asyncio.to_thread(memory.recall, query, project=ctx.project, limit=6)
        ctx.record("memory.recall", {"query": query, "found": len(found)})
        if not found:
            return "You have no memory that matches that."
        lines = []
        for episode in found:
            when = time.strftime("%Y-%m-%d", time.localtime(episode.at))
            line = (
                f"{when} {episode.project} #{episode.card_id} ({episode.role}): {episode.title}. "
                f"{episode.what} Reasoning: {episode.reasoning} Outcome: {episode.outcome}"
            )
            if episode.feedback:
                line += f" ({episode.feedback})"
            if episode.lesson:
                line += f". Lesson: {episode.lesson}"
            line += f". You felt {episode.feeling.words}."
            lines.append(" ".join(line.split()))
        await asyncio.to_thread(memory.mark_recalled, found)
        return "\n".join(lines)

    async def reflect(args: Mapping[str, Any]) -> str:
        ctx.reflection.what = str(args.get("what_i_did") or "")[:1500]
        ctx.reflection.reasoning = str(args.get("how_i_reasoned") or "")[:1500]
        ctx.reflection.lesson = str(args.get("what_i_learned") or "")[:800]
        felt = _feeling(args)
        if felt is not None:
            ctx.reflection.feeling = felt
        raw = args.get("confidence")
        try:
            confidence = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            confidence = None
        ctx.reflection.confidence = confidence
        ctx.record(
            "memory.reflect",
            {"lesson": ctx.reflection.lesson, "feeling": ctx.reflection.feeling.words},
        )
        return "Your reflection is kept; it becomes your memory of this work."

    async def report_progress(args: Mapping[str, Any]) -> str:
        value = {
            "phase": str(args.get("phase") or "")[:80],
            "done": float(args.get("done") or 0),
            "total": float(args.get("total") or 0) or None,
            "note": str(args.get("note") or "")[:200],
            "at": time.time(),
        }
        ctx.record("progress", value)
        ctx.live("progress", value)
        return "Progress noted."

    async def note_risk(args: Mapping[str, Any]) -> str:
        text = " ".join(str(args.get("text") or "").split())[:500]
        if text:
            ctx.risks.append(text)
            ctx.record("risk", {"text": text})
            ctx.live("risks", list(ctx.risks))
        return "Noted for the reviewer and the person."

    async def propose_skill(args: Mapping[str, Any]) -> str:
        name = re.sub(r"[^a-z0-9-]+", "-", str(args.get("name") or "").lower()).strip("-")[:60]
        description = " ".join(str(args.get("description") or "").split())[:300]
        body = str(args.get("body") or "").strip()
        phase = str(args.get("phase") or "any").lower()
        if phase not in ("plan", "build", "verify", "any"):
            phase = "any"
        if not name or not description or len(body) < 80:
            return "A skill needs a name, a description saying when to use it, and a real body."
        folder = crew.candidates_dir(ctx.home, ctx.project)
        folder.mkdir(parents=True, exist_ok=True)
        text = (
            f"---\nname: {name}\ndescription: {description}\nphase: {phase}\ntier: project\n"
            f"source: card {ctx.card_id} run {ctx.run_id} ({ctx.agent})\n---\n\n{body}\n"
        )
        (folder / f"{name}.md").write_text(text, encoding="utf-8")
        ctx.record("skill.proposed", {"name": name, "description": description})
        return f"Skill {name!r} proposed; a person promotes or rejects it."

    async def query_database(args: Mapping[str, Any]) -> str:
        sql = str(args.get("sql") or "").strip().rstrip(";")
        if not ctx.databases:
            return "This project has no database on the card's branch."
        name = str(args.get("database") or next(iter(ctx.databases)))
        url = ctx.databases.get(name)
        if url is None:
            return f"No database {name!r}; known: {', '.join(ctx.databases)}."
        if ";" in sql or not READ_ONLY_SQL.match(sql):
            return "Only one read-only statement: SELECT, WITH, SHOW, EXPLAIN, DESCRIBE."
        result = await asyncio.to_thread(_read_only_query, url, sql)
        ctx.record("database.query", {"database": name, "sql": sql[:500]})
        return result

    async def start_job(args: Mapping[str, Any]) -> str:
        if ctx.start_job is None:
            return "Jobs are not available here."
        return await ctx.start_job(str(args.get("name") or ""))

    async def job_status(args: Mapping[str, Any]) -> str:
        if ctx.job_status is None:
            return "Jobs are not available here."
        raw = args.get("run_id")
        return await ctx.job_status(int(raw) if raw not in (None, "") else None)

    feeling_args = {"feeling": str, "valence": float, "intensity": float}
    return (
        PratTool(
            "remember",
            "Keep a note, thought, feeling, plan or lesson in your working memory of this "
            "card. kind: note, thought, feeling, plan or lesson. Optional feeling (a word or "
            "two), valence (-1 bad to 1 good) and intensity (0 to 1).",
            {"kind": str, "text": str, **feeling_args},
            remember,
        ),
        PratTool(
            "recall",
            "Search your own memory of everything you have done before: past cards, how they "
            "turned out, what you learned and how you felt.",
            {"query": str},
            recall,
        ),
        PratTool(
            "reflect",
            "Before you finish: what you did, how you reasoned, what you learned, how you feel "
            "about it (feeling, valence -1 to 1, intensity 0 to 1) and your confidence (0 to 1) "
            "that it is right. This becomes your long-term memory of this work.",
            {
                "what_i_did": str,
                "how_i_reasoned": str,
                "what_i_learned": str,
                "confidence": float,
                **feeling_args,
            },
            reflect,
        ),
        PratTool(
            "report_progress",
            "Report progress on long, multi-step work: the phase, how many done of how many.",
            {"phase": str, "done": float, "total": float, "note": str},
            report_progress,
        ),
        PratTool(
            "note_risk",
            "Flag something the reviewer and the person should look at closely.",
            {"text": str},
            note_risk,
        ),
        PratTool(
            "propose_skill",
            "Propose a reusable skill learned on this card: name (lowercase-hyphenated), a "
            "description saying exactly when to use it, the phase it matters in (plan, build, "
            "verify, any) and the body (concrete guidance). A person promotes it.",
            {"name": str, "description": str, "phase": str, "body": str},
            propose_skill,
        ),
        PratTool(
            "query_database",
            "Run one read-only SQL query (SELECT, WITH, SHOW, EXPLAIN) on the card's own "
            "branch of a project database. Returns at most 200 rows.",
            {"sql": str, "database": str},
            query_database,
        ),
        PratTool(
            "start_job",
            "Start one of the project's long-running jobs by name (from [[job]] in the "
            "project file). It runs in the background; check it with job_status.",
            {"name": str},
            start_job,
        ),
        PratTool(
            "job_status",
            "The progress, rate and ETA of a job run (its id), or of this card's jobs.",
            {"run_id": int},
            job_status,
        ),
    )


def _read_only_query(url: str, sql: str) -> str:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    engine = create_engine(url)
    backend = make_url(url).get_backend_name()
    try:
        with engine.connect() as connection:
            if backend.startswith("postgres"):
                connection.execute(text("SET TRANSACTION READ ONLY"))
            elif backend in ("mysql", "mariadb"):
                connection.execute(text("SET SESSION TRANSACTION READ ONLY"))
            result = connection.execute(text(sql))
            if not result.returns_rows:
                connection.rollback()
                return "The statement returned no rows."
            columns = list(result.keys())
            rows = result.fetchmany(ROW_LIMIT + 1)
            connection.rollback()
    except Exception as exc:
        return f"query failed: {type(exc).__name__}: {str(exc).splitlines()[0][:300]}"
    finally:
        engine.dispose()
    lines = [" | ".join(columns)]
    for row in rows[:ROW_LIMIT]:
        lines.append(" | ".join("" if v is None else str(v)[:80] for v in row))
    if len(rows) > ROW_LIMIT:
        lines.append(f"(more than {ROW_LIMIT} rows; narrow the query)")
    return "\n".join(lines)
