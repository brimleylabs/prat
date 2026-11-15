"""Consolidation: during a dream, each agent turns its recent episodes into beliefs.

People consolidate memory in sleep; PRAT's agents do it in its dreams. For every agent with new
episodes since its last consolidation, one short session with no tools reads the agent's beliefs
and its recent episodes (what it did, how it reasoned and felt, how the work turned out) and
answers with the beliefs it now holds: lessons confirmed get stronger, ones contradicted by
outcomes are revised or dropped, new ones are formed from at least two episodes. The working
memory of finished cards is folded into their episodes and cleared.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prat.memory import AgentMemory, Belief, all_agents
from prat.policy import Decision
from prat.runtime.base import AgentRuntime, AgentTask, Budget

MIN_NEW_EPISODES = 2
MAX_BELIEFS = 24
FINAL = ("landed", "failed", "cancelled")
STATE_FILE = "consolidated.json"

PROMPT = """\
You are {agent}, an agent on a software team. Below are the beliefs you hold from experience and
the episodes you have lived since you last reflected: the work you did, how you reasoned, how you
felt, and how it turned out (landed, sent back with feedback, failed).

Consolidate them into the beliefs you now hold. Rules:
- A belief is one sentence, concrete and useful for future work ("In shop, migrations must be
  reversible because deploys roll back", not "be careful").
- Keep beliefs the episodes confirm and raise their strength (0 to 1); revise or drop beliefs
  an outcome contradicted; form new beliefs only from at least two episodes, or from one sent
  back with clear feedback.
- A belief that only holds for one project names it in "project"; otherwise project is null.
- At most {limit} beliefs. Cite the episode ids each one rests on.

Answer with JSON only:
{{"beliefs": [{{"text": "...", "project": "shop" or null, "strength": 0.7, "evidence": ["id"]}}]}}

Your beliefs now:
{beliefs}

Your episodes since then:
{episodes}
"""


@dataclass
class ConsolidationReport:
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"agents": self.agents, "cost_usd": round(self.cost_usd, 6)}


def _state(home: Path, agent: str) -> dict[str, Any]:
    try:
        return dict(
            json.loads((AgentMemory(home, agent).root / STATE_FILE).read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, TypeError):
        return {}


def _save_state(home: Path, agent: str, state: dict[str, Any]) -> None:
    path = AgentMemory(home, agent).root / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def fold_working(home: Path, agent: str) -> int:
    """Fold the working memory of finished cards into their episodes. Returns cards folded."""
    memory = AgentMemory(home, agent)
    if not memory.working_dir.is_dir():
        return 0
    episodes = memory.episodes()
    folded = 0
    for path in sorted(memory.working_dir.glob("*.json")):
        match = re.match(r"(?P<project>.+)-(?P<card>\d+)$", path.stem)
        if match is None:
            continue
        project, card_id = match.group("project"), int(match.group("card"))
        mine = [e for e in episodes if e.project == project and e.card_id == card_id]
        if not mine or mine[-1].outcome not in FINAL:
            continue
        notes = [n for n in memory.working(project, card_id) if n.kind != "thought"]
        if notes:
            latest = mine[-1]
            kept = " ".join(f"[{n.kind}] {n.text}" for n in notes[-8:])
            latest.reasoning = f"{latest.reasoning} Notes: {kept}".strip()[:3000]
            memory.save(latest)
        memory.forget_working(project, card_id)
        folded += 1
    return folded


def parse_beliefs(text: str, known_ids: set[str]) -> list[Belief]:
    match = re.search(r"\{.*\}", text or "", re.S)
    if match is None:
        return []
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return []
    found = []
    for item in (data.get("beliefs") if isinstance(data, dict) else None) or []:
        if not isinstance(item, dict) or not str(item.get("text") or "").strip():
            continue
        try:
            strength = max(0.0, min(1.0, float(item.get("strength") or 0.5)))
        except (TypeError, ValueError):
            strength = 0.5
        project = item.get("project")
        found.append(
            Belief(
                id=uuid.uuid4().hex[:10],
                text=" ".join(str(item["text"]).split())[:300],
                project=str(project) if project else None,
                strength=strength,
                evidence=[str(e) for e in item.get("evidence") or [] if str(e) in known_ids][:10],
            )
        )
    return found[:MAX_BELIEFS]


async def consolidate(
    home: Path,
    runtime: AgentRuntime | None,
    *,
    model: str | None,
    should_stop: Callable[[], bool] = lambda: False,
) -> ConsolidationReport:
    from prat.runtime.session import collect

    report = ConsolidationReport()
    for agent in all_agents(home):
        if should_stop():
            break
        folded = fold_working(home, agent)
        memory = AgentMemory(home, agent)
        state = _state(home, agent)
        since = float(state.get("at") or 0.0)
        fresh = [
            e for e in memory.episodes() if e.at > since or e.outcome != state.get(e.id, e.outcome)
        ]
        entry: dict[str, Any] = {"folded": folded, "new_episodes": len(fresh)}
        report.agents[agent] = entry
        if runtime is None or len(fresh) < MIN_NEW_EPISODES:
            continue
        beliefs = memory.beliefs()
        workdir = memory.root
        workdir.mkdir(parents=True, exist_ok=True)
        episodes_text = "\n".join(
            f"- id {e.id} ({time.strftime('%Y-%m-%d', time.localtime(e.at))}, {e.project} "
            f"#{e.card_id}, {e.role}): {e.title}. Did: {e.what[:400]} Reasoned: "
            f"{e.reasoning[:300]} Felt: {e.feeling.words}. Outcome: {e.outcome}"
            + (f" ({e.feedback[:200]})" if e.feedback else "")
            + (f". Learned: {e.lesson[:200]}" if e.lesson else "")
            for e in fresh[-30:]
        )
        prompt = PROMPT.format(
            agent=agent,
            limit=MAX_BELIEFS,
            beliefs="\n".join(f"- ({b.strength:.1f}) {b.text}" for b in beliefs) or "- none yet",
            episodes=episodes_text,
        )
        task = AgentTask(
            prompt=prompt,
            workdir=workdir,
            allowed_tools=frozenset(),
            policy=lambda _call: Decision.deny("consolidation uses no tools"),
            budget=Budget(max_usd=0.5, max_turns=2),
            model=model,
            metadata={"role": "librarian", "agent": agent},
        )
        out = await collect(runtime, task)
        report.cost_usd += out.cost_usd
        known = {e.id for e in memory.episodes()}
        new = parse_beliefs(out.final_text, known)
        if new:
            # A belief that survives keeps its first formation time.
            previous = {b.text.lower(): b for b in beliefs}
            for belief in new:
                old = previous.get(belief.text.lower())
                if old is not None:
                    belief.id, belief.formed_at = old.id, old.formed_at
            memory.save_beliefs(new)
            memory.compact()
            entry["beliefs"] = len(new)
        _save_state(
            home, agent, {"at": time.time(), **{e.id: e.outcome for e in memory.episodes()}}
        )
    return report
