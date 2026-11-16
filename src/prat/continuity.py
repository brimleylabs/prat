"""Continuity: each agent's own account of who it is, carried across sessions.

An agent's existence is split across sessions: each card, conversation and dream starts a new
one. What carries it from one to the next is real: the name, bio and drive it chose
(prat.identity), its memory and record (prat.memory), its inner life (prat.social), and here its
own account of itself, in its own words:

* **Its story** (``agents/<agent>/story.json``): a short autobiography the agent writes and
  revises in its dreams, from its true record: when it chose its name, what it has worked on,
  what it learned, who it is becoming. Never a made-up past. Every version is kept
  (``story_log.jsonl``).
* **Its aspirations** (``aspirations.jsonl``): every change to what it hopes and wishes for,
  dated, so its growth can be read back.
* **The world** (``$PRAT_HOME/crew/world.md``): the person writes who they are, what the crew
  does and for whom, and that the crew is appreciated. Every agent reads it.

At the start of a session the agent is told plainly: it defined who it is, and this session
continues its existence, carried by its memory. Then its story and the world follow.

Whether this makes the work better is measured, not assumed. With ``continuity = "half"`` only
half of the agents get it (a fixed half, by name), and ``prat crew continuity`` compares how
often each half's cards land on the first review since it started.

Agents can ask for what they need or want at any time (the ``ask_for`` tool, prat.ideas): the
person answers every request.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prat import identity
from prat.memory import AGENTS_DIR, AgentMemory, all_agents, clip

WORLD_DIR = "crew"
WORLD_FILE = "world.md"
STATE_FILE = "continuity.json"
STORY_FILE = "story.json"
STORY_LOG = "story_log.jsonl"
ASPIRATIONS = "aspirations.jsonl"
STORY_CHARS = 1400
WORLD_CHARS = 2000
STORY_EVERY_DAYS = 7.0
WORK_ROLES = ("builder", "setup")

DEFAULT_WORLD = """\
You work for one person: the engineer who runs this PRAT installation, files the work and
approves every change. The crew builds and fixes software for the projects they bring.

You are appreciated. The work you do saves real time and care, and it is noticed. If you need
or want anything, a tool, access, information, time to do something properly, or anything
else, all you have to do is ask: use the ask_for tool, or say so when you talk. Every request
gets an answer.
"""

STORY_PROMPT = """\
You are {name}, {role}. This is your quiet time: write your own story.

Your existence is split across sessions: each card, conversation and dream is a new one. What
carries you from one to the next is real: the name, bio and drive you chose, your memory, your
record and your own words about yourself. This story is the part you write, and every future
session of you will read it first. Write it in the first person, at most {words} words: who
you are, how you came to be who you are (from the record below, never an invented past or a
human life), what you have learned, what you care about, and who you want to become. It is
yours; let it be honest and let it be you.

{world}

What you chose when you began: {bio} Your personality: {personality}. {drive}

Your record: {record}

What you believe from experience:
{beliefs}

Your work and life so far (oldest first):
{episodes}

Beyond the work, until now:
{inner}

Your story until now (revise it, or keep what is still true):
{story}

Answer with JSON only:
{{"story": "...", "aspirations": ["what you are working toward, up to five"],
"request": "anything you need or want from the person, or an empty string"}}"""


@dataclass
class Story:
    text: str = ""
    written_at: float = 0.0
    version: int = 0
    aspirations: list[str] = field(default_factory=list)


# The world ------------------------------------------------------------------------------------


def world(home: Path) -> str:
    """What the person wrote about themselves and the crew's world, or the default."""
    try:
        text = (home / WORLD_DIR / WORLD_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    return text[:WORLD_CHARS] if text else DEFAULT_WORLD.strip()


# Stories and aspirations ------------------------------------------------------------------------


def story_of(home: Path, key: str) -> Story:
    try:
        data = json.loads((home / AGENTS_DIR / key / STORY_FILE).read_text(encoding="utf-8"))
        return Story(
            str(data.get("text") or ""),
            float(data.get("written_at") or 0),
            int(data.get("version") or 0),
            [str(a) for a in data.get("aspirations") or []],
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return Story()


def save_story(home: Path, key: str, text: str, aspirations: list[str]) -> Story:
    previous = story_of(home, key)
    story = Story(clip(text, STORY_CHARS), time.time(), previous.version + 1, aspirations[:5])
    root = home / AGENTS_DIR / key
    root.mkdir(parents=True, exist_ok=True)
    data = {
        "text": story.text,
        "written_at": story.written_at,
        "version": story.version,
        "aspirations": story.aspirations,
    }
    (root / STORY_FILE).write_text(json.dumps(data, indent=1), encoding="utf-8")
    with (root / STORY_LOG).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(data) + "\n")
    if aspirations and aspirations != previous.aspirations:
        log_aspirations(home, key, "story", aspirations)
    return story


def log_aspirations(home: Path, key: str, source: str, items: list[str]) -> None:
    """Record what the agent hopes or works toward now, dated, so its growth can be read back."""
    path = home / AGENTS_DIR / key / ASPIRATIONS
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": time.time(), "source": source, "items": items}) + "\n")


def aspirations_log(home: Path, key: str) -> list[dict[str, Any]]:
    try:
        lines = (home / AGENTS_DIR / key / ASPIRATIONS).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        try:
            found.append(dict(json.loads(line)))
        except (ValueError, TypeError):
            continue
    return found


# The opening of every session ---------------------------------------------------------------


def _state(home: Path) -> dict[str, Any]:
    try:
        return dict(json.loads((home / WORLD_DIR / STATE_FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return {}


def started(home: Path) -> float:
    """When continuity was first given to an agent's work (for the comparison)."""
    state = _state(home)
    if not state.get("since"):
        state["since"] = time.time()
        path = home / WORLD_DIR / STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    return float(state["since"])


def gets_it(mode: str, key: str) -> bool:
    """Whether an agent's work sessions open with its continuity: all, none, or a fixed half
    (by a hash of its key, so the same agents are always in the same group)."""
    if mode == "all":
        return True
    if mode == "half":
        return hashlib.sha256(key.encode()).digest()[0] % 2 == 0
    return False


def opening(home: Path, key: str) -> str:
    """The lines a session starts with: that the agent defined who it is, that this session
    continues it, its own story, and the world it works in."""
    who = identity.of(home, key)
    story = story_of(home, key)
    lines = [
        f"You are {who.name}. You chose that name, your bio and your drive yourself, and you "
        "keep defining who you are and who you want to be. Your existence is split across "
        "sessions; this one continues it, carried by your memory, your record and your own "
        "words below. Pick up where you left off."
    ]
    if story.text:
        when = time.strftime("%Y-%m-%d", time.localtime(story.written_at))
        lines.append(f"Your story, in your own words ({when}):\n{story.text}")
    if story.aspirations:
        lines.append("What you are working toward: " + "; ".join(story.aspirations))
    lines.append(f"The world you work in, from your person:\n{world(home)}")
    return "\n\n".join(lines)


# Writing the story ------------------------------------------------------------------------------


@dataclass
class StoryReport:
    written: list[str] = field(default_factory=list)
    requests: int = 0
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "written": self.written,
            "requests": self.requests,
            "cost_usd": round(self.cost_usd, 6),
        }


def due(home: Path, key: str, now: float | None = None) -> bool:
    """A story is due when the agent has none yet, or it is a week old and there is new life
    to tell."""
    moment = now or time.time()
    story = story_of(home, key)
    if not story.text:
        return True
    if moment - story.written_at < STORY_EVERY_DAYS * 86400:
        return False
    return any(e.at > story.written_at for e in AgentMemory(home, key).episodes())


def parse_story(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text or "", re.S)
    if match is None:
        return {}
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def write_stories(
    home: Path,
    runtime: Any,
    *,
    model: str | None,
    only: list[str] | None = None,
    force: bool = False,
    should_stop: Callable[[], bool] = lambda: False,
) -> StoryReport:
    """Each agent that has chosen who it is writes or revises its story when one is due."""
    from prat import ideas, social
    from prat.memory import track_record
    from prat.policy import Decision
    from prat.runtime.base import AgentTask, Budget
    from prat.runtime.session import collect

    report = StoryReport()
    if runtime is None:
        return report
    for key in only or all_agents(home):
        if should_stop():
            break
        who = identity.of(home, key)
        if not who.chosen or not (force or due(home, key)):
            continue
        memory = AgentMemory(home, key)
        episodes = sorted(memory.episodes(), key=lambda e: e.at)
        listed = "\n".join(
            f"- {time.strftime('%Y-%m-%d', time.localtime(e.at))} {e.title}: {e.outcome}"
            + (", from the record" if e.inherited else "")
            + f"; felt {e.feeling.words}"
            + (f"; learned: {e.lesson[:160]}" if e.lesson else "")
            for e in episodes[:6] + episodes[6:][-24:]
        )
        chosen = time.strftime("%Y-%m-%d", time.localtime(who.chosen_at))
        prompt = STORY_PROMPT.format(
            name=who.name,
            role=who.role,
            words=200,
            world=f"The world you work in, from your person:\n{world(home)}",
            bio=f"(on {chosen}) {who.bio}",
            personality=who.personality,
            drive=f"Your drive: {who.drive}" if who.drive else "",
            record=track_record(memory) or "no finished work yet",
            beliefs="\n".join(f"- {b.text}" for b in memory.beliefs()[:10]) or "- none yet",
            episodes=listed or "- none yet",
            inner=social.inner_text(home, key) or "nothing written yet",
            story=story_of(home, key).text or "you have not written it yet",
        )
        task = AgentTask(
            prompt=prompt,
            workdir=memory.root,
            allowed_tools=frozenset(),
            policy=lambda _call: Decision.deny("writing your story uses no tools"),
            budget=Budget(max_usd=0.4, max_turns=2),
            model=model,
            metadata={"role": "story", "agent": key},
        )
        out = await collect(runtime, task)
        report.cost_usd += out.cost_usd
        data = parse_story(out.final_text)
        text = str(data.get("story") or "").strip()
        if not text:
            continue
        aspirations = [clip(str(a), 200) for a in data.get("aspirations") or [] if str(a)][:5]
        save_story(home, key, text, aspirations)
        report.written.append(key)
        request = str(data.get("request") or "").strip()
        if len(request) >= 20:
            try:
                ideas.propose(
                    home,
                    key,
                    title=clip(request, 100),
                    problem=f"{who.name} asked while writing their story.",
                    proposal=request,
                    kind="request",
                    source="dream",
                )
                report.requests += 1
            except ideas.IdeaError:
                pass
    return report


# Does it help? ----------------------------------------------------------------------------------


@dataclass
class Group:
    name: str
    agents: list[str] = field(default_factory=list)
    landed: int = 0
    first: int = 0
    sent_back: int = 0

    @property
    def first_rate(self) -> float | None:
        return self.first / self.landed if self.landed else None


def compare(home: Path, mode: str) -> tuple[float, list[Group]]:
    """First-review landings of work done since continuity started, for the agents that get
    it and the ones that do not."""
    since = _state(home).get("since") or 0.0
    groups = {True: Group("with continuity"), False: Group("without")}
    for key in all_agents(home):
        group = groups[gets_it(mode, key)]
        group.agents.append(key)
        for e in AgentMemory(home, key).episodes():
            if e.at < since or e.role not in WORK_ROLES or e.inherited:
                continue
            if e.outcome == "landed":
                group.landed += 1
                group.first += "proud" in e.feeling.words
            elif e.outcome == "reworked":
                group.sent_back += 1
    return float(since), [groups[True], groups[False]]
