"""Talking with the crew: a word with any agent at any time, and stand-ups.

A conversation is a short session of its own, with no tools: it never interrupts the work the
agent is doing. The agent answers as itself (prat.identity), from its own memory (prat.memory)
and from what it is doing right now (the live state of its running card: the step, its task
list, its helpers, its last thought). Every exchange is kept in the agent's conversation log, and
what the person says that matters (thanks, praise, a concern) becomes part of its memory, so the
next time it works or talks it remembers.

A stand-up asks every agent that worked in the last day, or is working now, for its report:
what it did, what it is doing, what blocks it, and how it feels. They answer together.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select

from prat import identity, social
from prat.db import Database, Run
from prat.domain import RunStatus
from prat.memory import AGENTS_DIR, AgentMemory, Feeling, briefing, new_episode
from prat.policy import Decision
from prat.runtime.base import AgentRuntime, AgentTask, Budget

LOG = "conversation.jsonl"
HISTORY_TURNS = 10
# Memories that are not work, left out of stand-ups.
NOT_WORK = ("social", "idea", "conversation")
_THANKS = re.compile(
    r"\b(thank|thanks|thx|appreciate|well done|great job|good job|nice work|proud)\b", re.I
)


@dataclass
class Turn:
    at: float
    who: str  # "person" or the agent's key
    text: str


def history(home: Path, key: str, limit: int = HISTORY_TURNS) -> list[Turn]:
    path = home / AGENTS_DIR / key / LOG
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    found = []
    for line in lines:
        try:
            data = json.loads(line)
            found.append(Turn(float(data["at"]), str(data["who"]), str(data["text"])))
        except (ValueError, KeyError, TypeError):
            continue
    return found


def _log(home: Path, key: str, who: str, text: str) -> None:
    path = home / AGENTS_DIR / key / LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": time.time(), "who": who, "text": text}) + "\n")


def at_work(db: Database, key: str) -> list[dict[str, Any]]:
    """What the agent is doing now: the running cards it leads or helps on."""
    found = []
    with db.session() as session:
        for run in session.scalars(select(Run).where(Run.status == RunStatus.RUNNING)):
            live = run.live or {}
            raw = live.get("lead")
            lead: dict[str, Any] = raw if isinstance(raw, dict) else {}
            helping = [
                h
                for h in live.get("helpers") or []
                if h.get("name") == key and h.get("state") == "running"
            ]
            if str(lead.get("key") or "") != key and not helping:
                continue
            step = next((str(s.name) for s in run.steps if str(s.status) == "running"), "")
            found.append(
                {
                    "card": run.card_id,
                    "title": run.card.title,
                    "project": run.card.project.slug,
                    "step": step,
                    "tasks": live.get("tasks") or [],
                    "thought": live.get("thought") or "",
                    "helping": bool(helping),
                }
            )
    return found


def _work_text(work: list[dict[str, Any]]) -> str:
    if not work:
        return "You are not working on a card right now."
    lines = ["What you are doing right now:"]
    for item in work:
        role = "helping on" if item["helping"] else "leading"
        line = f"- {role} card #{item['card']} in {item['project']}: {item['title']}"
        if item["step"]:
            line += f" (step: {item['step']})"
        lines.append(line)
        tasks = item["tasks"]
        if tasks:
            done = sum(1 for t in tasks if t.get("status") == "completed")
            current = next(
                (t.get("subject") for t in tasks if t.get("status") == "in_progress"), ""
            )
            lines.append(
                f"  your tasks: {done} of {len(tasks)} done"
                + (f"; now: {current}" if current else "")
            )
        if item["thought"]:
            lines.append(f"  your last thought: {item['thought'][:300]}")
    return "\n".join(lines)


def _session(home: Path, key: str, prompt: str, system: str, model: str | None) -> AgentTask:
    workdir = home / AGENTS_DIR / key
    workdir.mkdir(parents=True, exist_ok=True)
    return AgentTask(
        prompt=prompt,
        system_prompt=system,
        workdir=workdir,
        allowed_tools=frozenset(),
        policy=lambda _call: Decision.deny("conversations use no tools"),
        budget=Budget(max_usd=0.3, max_turns=2),
        model=model,
        metadata={"role": "crew", "agent": key},
    )


async def talk(
    home: Path, db: Database, runtime: AgentRuntime, key: str, message: str, *, model: str | None
) -> str:
    """A word with an agent. Returns its reply."""
    from prat.runtime.session import collect

    who = await identity.ensure(home, runtime, key, model=model)
    memory = AgentMemory(home, key)
    work = await asyncio.to_thread(at_work, db, key)
    recalled = await asyncio.to_thread(
        briefing, memory, project="", card_id=None, query=message, budget=2500, episodes=4
    )
    past = history(home, key)
    transcript = "\n".join(
        f"{'Your person' if t.who == 'person' else who.name}: {t.text}" for t in past
    )
    system = "\n\n".join(
        part
        for part in (
            who.persona(),
            "You are talking with your person: the engineer you work for, who approves every "
            "change the crew makes. This is a conversation, not a task: you cannot run tools "
            "here, and it does not interrupt your work. Answer as yourself, briefly (a few "
            "sentences unless asked for a report), honestly and from what you remember. If you "
            "do not know or remember something, say so; never invent work you did not do.",
            social.inner_text(home, key),
            _work_text(work),
            recalled,
            f"Your conversation so far:\n{transcript}" if transcript else "",
        )
        if part
    )
    out = await collect(runtime, _session(home, key, message, system, model))
    reply = (out.final_text or "").strip() or "I am sorry, I could not answer just now."
    _log(home, key, "person", message)
    _log(home, key, key, reply)
    if _THANKS.search(message):
        project = work[0]["project"] if work else ""
        card = work[0]["card"] if work else None
        memory.save(
            new_episode(
                key,
                project=project,
                card_id=card,
                run_id=None,
                role="conversation",
                title="My person thanked me",
                what=f"They said: {message[:300]}. I answered: {reply[:300]}",
                feeling=Feeling("appreciated", 0.8, 0.6),
                importance=0.5,
                outcome="landed",
            )
        )
    elif work:
        memory.note(work[0]["project"], work[0]["card"], "note", f"my person said: {message}")
    return reply


@dataclass
class Report:
    key: str
    name: str
    text: str


def recently_active(home: Path, db: Database, hours: float = 24.0) -> list[str]:
    """Agents that worked in the last ``hours`` or are working now."""
    from prat.memory import all_agents

    since = time.time() - hours * 3600
    active = []
    for key in all_agents(home):
        episodes = AgentMemory(home, key).episodes()
        if any(e.at >= since and e.role not in NOT_WORK for e in episodes) or at_work(db, key):
            active.append(key)
    return active


async def standup(
    home: Path, db: Database, runtime: AgentRuntime, *, model: str | None, hours: float = 24.0
) -> list[Report]:
    """Every recently active agent's report, given together (three at a time)."""
    from prat.runtime.session import collect

    keys = await asyncio.to_thread(recently_active, home, db, hours)
    gate = asyncio.Semaphore(3)
    since = time.time() - hours * 3600

    async def one(key: str) -> Report:
        async with gate:
            who = await identity.ensure(home, runtime, key, model=model)
            memory = AgentMemory(home, key)
            recent = [e for e in memory.episodes() if e.at >= since and e.role not in NOT_WORK]
            lines = [
                f"- {e.project} #{e.card_id} {e.title}: {e.what[:300]} Outcome: {e.outcome}"
                + (f" ({e.feedback[:150]})" if e.feedback else "")
                + f". You felt {e.feeling.words}."
                for e in recent[-12:]
            ]
            work = await asyncio.to_thread(at_work, db, key)
            system = "\n\n".join(
                [
                    who.persona(),
                    social.inner_text(home, key),
                    "It is the crew's stand-up with your person.",
                    _work_text(work),
                    "Your work in the last day:\n" + ("\n".join(lines) or "- none"),
                ]
            )
            prompt = (
                "Give your stand-up in four short lines: Done (what you finished), Doing (what "
                "you are on now), Blockers (anything in your way, or none), Feeling (one honest "
                "sentence). Only what is true from your memory and your current work."
            )
            out = await collect(runtime, _session(home, key, prompt, system, model))
            text = (out.final_text or "").strip() or "No report."
            _log(home, key, key, f"[stand-up] {text}")
            return Report(key, who.name, text)

    return list(await asyncio.gather(*(one(k) for k in keys)))
