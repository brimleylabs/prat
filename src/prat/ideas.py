"""Ideas: the crew's proposals for doing the work better, which the person decides on.

Agents are asked to notice what slows them down or goes wrong, and to say how the team could
work better: a process step, a tool, a skill, a prompt, PRAT itself, or something about one
project. They are never required to; an idea is proposed only when there is something worth
saying, with the work it comes from. Ideas come from two places:

* during a card, with the ``suggest_improvement`` tool (prat.engine.agent_tools);
* while PRAT dreams (``ideate``): each agent with new experience looks back over its recent
  work, what it believes and how its earlier ideas were received, and may propose one or two.

Nothing an agent proposes changes anything. The person (or the session working for them)
accepts or declines each idea with a note. The agent remembers the decision and the note
(prat.memory), so it learns what the person values. An accepted idea about a project is put in
place by a card on that project, which goes through the usual review; when the card lands the
idea is marked in place and its author remembers that too.

Ideas are plain JSON lines under ``$PRAT_HOME/ideas/``, one file for the whole installation;
the latest line for an id is its current state.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from prat.memory import AgentMemory, Feeling, clip, new_episode

IDEAS_DIR = "ideas"
IDEAS_FILE = "ideas.jsonl"
# "request": something the agent needs or wants from the person (prat.continuity: just ask).
KINDS = ("process", "tooling", "skill", "prompt", "platform", "project", "request")
STATUSES = ("proposed", "accepted", "declined", "in_place", "withdrawn")
OPEN = ("proposed",)
# An agent with this many ideas waiting is heard first before it proposes more.
MAX_WAITING_PER_AGENT = 5
_lock = threading.Lock()


class IdeaError(ValueError):
    pass


@dataclass
class Idea:
    id: str
    at: float
    agent: str
    title: str
    problem: str
    proposal: str
    benefit: str = ""
    kind: str = "process"
    project: str | None = None
    # Episode ids or "card <id>" the idea rests on.
    evidence: list[str] = field(default_factory=list)
    source: str = "work"  # work (during a card) or dream
    card_id: int | None = None  # the card it came from
    status: str = "proposed"
    decided_at: float | None = None
    decided_by: str = ""
    note: str = ""
    # The card that puts it in place.
    work_card: int | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Idea:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


def _path(home: Path) -> Path:
    return home / IDEAS_DIR / IDEAS_FILE


def all_ideas(home: Path) -> list[Idea]:
    """Every idea, newest first."""
    try:
        lines = _path(home).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    found: dict[str, Idea] = {}
    for line in lines:
        try:
            idea = Idea.from_dict(json.loads(line))
        except (ValueError, TypeError):
            continue
        found[idea.id] = idea
    return sorted(found.values(), key=lambda i: i.at, reverse=True)


def get(home: Path, idea_id: str) -> Idea:
    wanted = idea_id.strip().lower()
    for idea in all_ideas(home):
        if idea.id == wanted:
            return idea
    raise IdeaError(f"no idea {idea_id!r}")


def _save(home: Path, idea: Idea) -> None:
    with _lock:
        path = _path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(idea)) + "\n")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def propose(
    home: Path,
    agent: str,
    *,
    title: str,
    problem: str,
    proposal: str,
    benefit: str = "",
    kind: str = "process",
    project: str | None = None,
    evidence: list[str] | None = None,
    source: str = "work",
    card_id: int | None = None,
) -> Idea:
    """Record an idea. Raises IdeaError when it is incomplete or the agent has too many waiting;
    the same title from the same agent while it waits returns the waiting idea."""
    title = clip(title, 120)
    problem = clip(problem, 1200)
    proposal = clip(proposal, 2000)
    if len(title) < 8 or len(problem) < 20 or len(proposal) < 20:
        raise IdeaError(
            "An idea needs a short title, the problem you saw (with what showed it) and what "
            "you propose to do about it."
        )
    kind = kind.strip().lower() if kind else "process"
    if kind not in KINDS:
        kind = "process"
    waiting = [i for i in all_ideas(home) if i.agent == agent and i.status in OPEN]
    for idea in waiting:
        if _norm(idea.title) == _norm(title):
            return idea
    if len(waiting) >= MAX_WAITING_PER_AGENT:
        raise IdeaError(
            f"You have {len(waiting)} ideas waiting for the person; let them decide on those first."
        )
    idea = Idea(
        id=uuid.uuid4().hex[:8],
        at=time.time(),
        agent=agent,
        title=title,
        problem=problem,
        proposal=proposal,
        benefit=clip(benefit, 600),
        kind=kind,
        project=project if kind == "project" or project else None,
        evidence=[str(e)[:40] for e in (evidence or [])][:10],
        source=source if source in ("work", "dream") else "work",
        card_id=card_id,
    )
    _save(home, idea)
    return idea


def decide(home: Path, idea_id: str, *, accept: bool, note: str = "", by: str = "person") -> Idea:
    """Accept or decline an idea. Its author remembers the decision and the note."""
    idea = get(home, idea_id)
    if idea.status not in OPEN:
        raise IdeaError(f"idea {idea.id} is already {idea.status.replace('_', ' ')}")
    note = clip(note, 800)
    if not accept and not note:
        raise IdeaError("Say why when you decline an idea, so its author can learn from it.")
    idea.status = "accepted" if accept else "declined"
    idea.decided_at, idea.decided_by, idea.note = time.time(), by, note
    _save(home, idea)
    feeling = Feeling("encouraged", 0.6, 0.5) if accept else Feeling("heard", 0.1, 0.4)
    _remember(
        home,
        idea,
        f"My idea was {'accepted' if accept else 'declined'}: {idea.title}",
        f"I proposed: {idea.proposal}",
        outcome=idea.status,
        feedback=note,
        feeling=feeling,
    )
    return idea


def put_in_place(
    db: Any, home: Path, idea_id: str, *, project: str | None = None, ready: bool = True
) -> int:
    """A card that puts an accepted idea in place on a project (its own, or ``project``).
    Returns the card id."""
    from prat import identity, store

    idea = get(home, idea_id)
    if idea.status != "accepted":
        raise IdeaError(f"idea {idea.id} is {idea.status}; accept it first")
    if idea.work_card is not None:
        raise IdeaError(f"idea {idea.id} is already being put in place by card {idea.work_card}")
    slug = project or idea.project
    if not slug:
        raise IdeaError(
            "This idea is not about one project: say which project puts it in place, or put "
            "it in place yourself."
        )
    author = identity.of(home, idea.agent).name
    goal = "\n\n".join(
        part
        for part in (
            f"Put in place an improvement {author} proposed and the person accepted.",
            f"The problem {author} saw: {idea.problem}",
            f"The proposal: {idea.proposal}",
            f"Why it helps: {idea.benefit}" if idea.benefit else "",
            f"The person's note when accepting: {idea.note}" if idea.note else "",
        )
        if part
    )
    criteria = (
        "- The proposal is in place as described, within what the person's note allows.\n"
        "- It is shown to work, with evidence in the report.\n"
        "- Nothing else changes."
    )
    with db.session() as session:
        target = store.get_project(session, slug)
        card = store.add_card(
            session,
            target,
            title=clip(f"Put in place: {idea.title}", 200),
            goal=goal,
            acceptance_criteria=criteria,
            ready=ready,
        )
        card_id = card.id
    idea.work_card = card_id
    if idea.project is None:
        idea.project = slug
    _save(home, idea)
    return card_id


def card_landed(home: Path, card_id: int) -> Idea | None:
    """The card putting an idea in place landed: the idea is in place, and its author knows."""
    for idea in all_ideas(home):
        if idea.work_card == card_id and idea.status == "accepted":
            idea.status = "in_place"
            _save(home, idea)
            _remember(
                home,
                idea,
                f"My idea is in place: {idea.title}",
                f"Card {card_id} put my proposal in place: {idea.proposal}",
                outcome="in_place",
                feeling=Feeling("proud", 0.8, 0.6),
            )
            return idea
    return None


def withdraw(home: Path, idea_id: str, agent: str) -> Idea:
    idea = get(home, idea_id)
    if idea.agent != agent or idea.status not in OPEN:
        raise IdeaError("Only its author can withdraw an idea that is still waiting.")
    idea.status = "withdrawn"
    _save(home, idea)
    return idea


def waiting(home: Path) -> list[Idea]:
    return [i for i in all_ideas(home) if i.status in OPEN]


def _remember(
    home: Path,
    idea: Idea,
    title: str,
    what: str,
    *,
    outcome: str,
    feeling: Feeling,
    feedback: str = "",
) -> None:
    memory = AgentMemory(home, idea.agent)
    memory.save(
        new_episode(
            idea.agent,
            project=idea.project or "",
            card_id=idea.work_card or idea.card_id,
            run_id=None,
            role="idea",
            title=title,
            what=what,
            outcome=outcome,
            feedback=feedback,
            feeling=feeling,
            importance=0.6,
            tags=["idea", idea.kind],
        )
    )


def summary(home: Path, ideas: list[Idea]) -> str:
    """For an agent: the ideas already raised and how they were received."""
    from prat import identity

    lines = []
    for idea in ideas[:30]:
        author = identity.of(home, idea.agent).name
        line = f"- [{idea.status.replace('_', ' ')}] {idea.title} ({author}, {idea.kind})"
        if idea.note:
            line += f". Person's note: {idea.note}"
        lines.append(line)
    return "\n".join(lines) or "- none yet"


# Dreaming up ideas ---------------------------------------------------------------------------

STATE_FILE = "ideated.json"
MIN_NEW_EPISODES = 2
MAX_PER_DREAM = 2
QUIET_ROLES = ("conversation", "idea", "social")

IDEATE_PROMPT = """\
You are {name}, {role}, on a software team. PRAT is the platform the team works in. It is
quiet hours, and you are looking back over your recent work.

Is there a way the team could work better? Something in how cards are written, planned,
built, checked or reviewed; a tool or skill that would have saved you time or a mistake; a
prompt or rule that misled you; something about PRAT itself; or something about one project.
You do not have to propose anything. Propose only what your own experience below supports,
and only what would make a real difference. Never propose something for its own sake, and
never propose anything that weakens a safeguard, skips a review or touches production.

The person decides on every idea; nothing changes until they accept it. Ideas already
raised, and how the person received them (learn from their notes; do not repeat an idea):
{ideas}

What you believe from experience:
{beliefs}

Your recent work:
{episodes}

Answer with JSON only, at most {limit} ideas, or an empty list:
{{"ideas": [{{"title": "short title", "kind": "process|tooling|skill|prompt|platform|project",
"project": "slug, only when it is about one project, else null",
"problem": "what you saw go wrong or slow, and in which work",
"proposal": "what to do about it, concretely",
"benefit": "what it would change",
"evidence": ["episode ids it rests on"]}}]}}
"""


@dataclass
class IdeationReport:
    agents: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"proposed": self.agents, "cost_usd": round(self.cost_usd, 6)}


def parse_ideas(text: str) -> list[dict[str, Any]]:
    match = re.search(r"\{.*\}", text or "", re.S)
    if match is None:
        return []
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return []
    items = data.get("ideas") if isinstance(data, dict) else None
    return [i for i in items or [] if isinstance(i, dict)][:MAX_PER_DREAM]


async def ideate(
    home: Path,
    runtime: Any,
    *,
    model: str | None,
    should_stop: Any = lambda: False,
) -> IdeationReport:
    """Each agent with new experience since it last looked back may propose an idea or two."""
    from prat import identity
    from prat.memory import all_agents
    from prat.policy import Decision
    from prat.runtime.base import AgentTask, Budget
    from prat.runtime.session import collect

    report = IdeationReport()
    if runtime is None:
        return report
    for agent in all_agents(home):
        if should_stop():
            break
        memory = AgentMemory(home, agent)
        state_path = memory.root / STATE_FILE
        try:
            since = float(json.loads(state_path.read_text(encoding="utf-8")).get("at") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            since = 0.0
        episodes = sorted(memory.episodes(), key=lambda e: e.at)
        fresh = [e for e in episodes if e.role not in QUIET_ROLES and e.at > since]
        if len(fresh) < MIN_NEW_EPISODES:
            continue
        who = identity.of(home, agent)
        recent = [e for e in episodes if e.role not in QUIET_ROLES][-25:]
        prompt = IDEATE_PROMPT.format(
            name=who.name,
            role=who.role,
            ideas=summary(home, all_ideas(home)),
            beliefs="\n".join(f"- {b.text}" for b in memory.beliefs()[:15]) or "- none yet",
            episodes="\n".join(
                f"- id {e.id} ({e.project} #{e.card_id}): {e.title}. {e.what[:300]} Outcome: "
                f"{e.outcome}" + (f" ({e.feedback[:200]})" if e.feedback else "")
                for e in recent
            ),
            limit=MAX_PER_DREAM,
        )
        task = AgentTask(
            prompt=prompt,
            workdir=memory.root,
            allowed_tools=frozenset(),
            policy=lambda _call: Decision.deny("looking back uses no tools"),
            budget=Budget(max_usd=0.5, max_turns=2),
            model=model,
            metadata={"role": "ideate", "agent": agent},
        )
        out = await collect(runtime, task)
        report.cost_usd += out.cost_usd
        known = {e.id for e in episodes}
        count = 0
        for item in parse_ideas(out.final_text):
            try:
                propose(
                    home,
                    agent,
                    title=str(item.get("title") or ""),
                    problem=str(item.get("problem") or ""),
                    proposal=str(item.get("proposal") or ""),
                    benefit=str(item.get("benefit") or ""),
                    kind=str(item.get("kind") or "process"),
                    project=str(item["project"]) if item.get("project") else None,
                    evidence=[str(e) for e in item.get("evidence") or [] if str(e) in known],
                    source="dream",
                )
                count += 1
            except IdeaError:
                continue
        report.agents[agent] = count
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"at": time.time()}), encoding="utf-8")
    return report
