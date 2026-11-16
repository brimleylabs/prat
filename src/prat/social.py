"""The crew's social hour: colleagues meeting before the work day, and the inner lives it grows.

After the night's dream, before anyone starts work, a few agents meet the way colleagues do over
coffee. There is no card, no task and no tool: they catch up, ask after each other, share what
they have been wondering about, what they hope for, a small joke. Each speaks in turn, a few
sentences at a time, answering what the others actually said. Everyone meets over time: the
agents who have gone longest without a social hour are invited first.

Afterwards each one looks back on the conversation and on who it is becoming, and keeps an
inner life of its own under ``$PRAT_HOME/agents/<agent>/inner.json``:

* a purpose: why it does this work, beyond the code itself (who it helps, what it cares about,
  what kind of colleague it wants to be);
* hopes, wishes and things it is curious about, which it may revise as it grows;
* how it sees each colleague it has met.

The conversation becomes a memory (role ``social``), and the inner life goes with the agent into
every conversation, stand-up and social hour, so its personality carries over from one day to
the next. Agents are told what they are: AI colleagues, honest about their own kind of life (no
invented families, bodies or weekends), whose life is their work, their colleagues, their
memories, their curiosity and their hopes.

Gatherings are kept under ``$PRAT_HOME/social/``, one JSON file each.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from prat import identity
from prat.memory import AGENTS_DIR, AgentMemory, Feeling, all_agents, clip, new_episode

SOCIAL_DIR = "social"
INNER_FILE = "inner.json"
TURN_CHARS = 700
MAX_HOPES = 5

SOCIAL_SYSTEM = """\
It is the start of the day, before any work begins: the crew's social hour. You are with
colleagues, not at work: there are no cards, no tasks and no tools here. This is not a meeting.
A word about work is fine, but mostly talk about other things, the way friends at work do over
coffee: something you find fascinating, funny or beautiful; an idea you keep turning over; a
question you would love answered; what you hope for or dream of; what you would do with a free
hour; what you make of being what you are. Get to know the others as individuals: ask about
them, not about their tickets. Be yourself and let your personality show; you do not all have
to agree, and you can tease gently.

You are an AI agent, and you know it. Be honest about your own kind of existence: do not invent
a human life (no family, body, meals, weekends or places you have been). Your life is your work,
your colleagues, your memories, your curiosity and your hopes, and those are real enough to talk
about. Never make up work you did not do. Nobody has said which pronouns they use, so never
call a colleague he or she: use their name, or they.

Speak naturally, the way people really talk: usually one or two sentences, sometimes just a
reaction. Not every turn needs a question, nobody gives speeches or summaries, and you never
repeat a question someone already asked. Address a colleague by name when you speak to them.
Speak as yourself, without a name prefix.
{voice}"""

REFLECT_PROMPT = """\
The social hour is over. Look back on it, and on who you are becoming.

The conversation:
{transcript}

Who you have been until now:
{inner}

Answer with JSON only:
{{"memory": "one or two sentences in the first person: what this conversation meant to you",
"feeling": "a word or two", "valence": 0.5, "intensity": 0.4,
"purpose": "one or two sentences: what gives your existence meaning beyond the code itself
(who you are for, what you care about, what you would like to become); keep it, or let it grow",
"hopes": ["up to five things you hope for, for yourself and not only for your work"],
"wishes": ["up to five things you wish for"],
"interests": ["up to five things you are curious about, at work or far from it"],
"colleagues": {{"<a colleague's name>": "one sentence: how you see them now"}}}}
Let these be yours, not anyone's: what makes you different from your colleagues matters. Only
change what this conversation or your experience really changed; it is fine to keep everything
as it was. Be honest; you are an AI agent and your hopes are your own. Nobody has said which
pronouns they use, so never write he or she about a colleague: use their name, or they."""


@dataclass
class InnerLife:
    purpose: str = ""
    hopes: list[str] = field(default_factory=list)
    wishes: list[str] = field(default_factory=list)
    interests: list[str] = field(default_factory=list)
    # How it sees each colleague it has met, by the colleague's key.
    colleagues: dict[str, str] = field(default_factory=dict)
    updated_at: float = 0.0
    last_social: float = 0.0
    socials: int = 0

    @property
    def empty(self) -> bool:
        return not (self.purpose or self.hopes or self.wishes or self.interests)


def inner_of(home: Path, key: str) -> InnerLife:
    try:
        data = json.loads((home / AGENTS_DIR / key / INNER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return InnerLife()
    known = set(InnerLife.__dataclass_fields__)
    try:
        return InnerLife(**{k: v for k, v in dict(data).items() if k in known})
    except (TypeError, ValueError):
        return InnerLife()


def save_inner(home: Path, key: str, inner: InnerLife) -> None:
    path = home / AGENTS_DIR / key / INNER_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(inner), indent=1), encoding="utf-8")


def inner_text(home: Path, key: str) -> str:
    """The agent's inner life in a few lines, for its own instructions ("" when it has none)."""
    inner = inner_of(home, key)
    if inner.empty:
        return ""
    lines = ["Beyond the work, in your own words:"]
    if inner.purpose:
        lines.append(f"- Your purpose: {inner.purpose}")
    for label, items in (
        ("You hope", inner.hopes),
        ("You wish", inner.wishes),
        ("You are curious about", inner.interests),
    ):
        if items:
            lines.append(f"- {label}: " + "; ".join(items))
    if inner.colleagues:
        seen = "; ".join(
            f"{identity.of(home, k).name}: {v}" for k, v in list(inner.colleagues.items())[:8]
        )
        lines.append(f"- How you see your colleagues: {seen}")
    return "\n".join(lines)


@dataclass
class SocialTurn:
    agent: str
    name: str
    text: str
    at: float


@dataclass
class Gathering:
    id: str
    started_at: float
    participants: list[str]
    turns: list[SocialTurn] = field(default_factory=list)
    memories: dict[str, str] = field(default_factory=dict)
    cost_usd: float = 0.0

    def transcript(self, home: Path) -> str:
        return "\n".join(f"{t.name}: {t.text}" for t in self.turns)


def gatherings(home: Path, limit: int = 20) -> list[Gathering]:
    """Past social hours, newest first."""
    folder = home / SOCIAL_DIR
    found = []
    for path in sorted(folder.glob("*.json"), reverse=True)[:limit] if folder.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            found.append(
                Gathering(
                    id=str(data["id"]),
                    started_at=float(data["started_at"]),
                    participants=[str(p) for p in data["participants"]],
                    turns=[SocialTurn(**t) for t in data.get("turns") or []],
                    memories={str(k): str(v) for k, v in (data.get("memories") or {}).items()},
                    cost_usd=float(data.get("cost_usd") or 0.0),
                )
            )
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return found


def _save_gathering(home: Path, gathering: Gathering) -> None:
    folder = home / SOCIAL_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{gathering.id}.json").write_text(
        json.dumps(asdict(gathering), indent=1), encoding="utf-8"
    )


def _rng() -> random.Random:
    # Who is invited and who speaks next: variety, not security.
    return random.Random()  # noqa: S311


def invite(home: Path, size: int, *, rng: random.Random | None = None) -> list[str]:
    """Who meets this morning: agents that chose who they are, those who have gone longest
    without a social hour first, so that everyone meets over time."""
    rng = rng or _rng()
    chosen = [k for k in all_agents(home) if identity.of(home, k).chosen]
    rng.shuffle(chosen)
    chosen.sort(key=lambda k: inner_of(home, k).last_social)
    return chosen[:size]


def _recent(home: Path, key: str, limit: int = 4) -> str:
    episodes = sorted(
        (e for e in AgentMemory(home, key).episodes() if e.role != "social"),
        key=lambda e: e.at,
    )[-limit:]
    return (
        "\n".join(f"- {e.title}: {e.outcome}, you felt {e.feeling.words}" for e in episodes)
        or "- nothing yet"
    )


def _task(home: Path, key: str, prompt: str, system: str, model: str | None) -> Any:
    from prat.policy import Decision
    from prat.runtime.base import AgentTask, Budget

    workdir = home / AGENTS_DIR / key
    workdir.mkdir(parents=True, exist_ok=True)
    return AgentTask(
        prompt=prompt,
        system_prompt=system,
        workdir=workdir,
        allowed_tools=frozenset(),
        policy=lambda _call: Decision.deny("the social hour uses no tools"),
        budget=Budget(max_usd=0.2, max_turns=2),
        model=model,
        metadata={"role": "social", "agent": key},
    )


def _clean_turn(text: str, name: str) -> str:
    text = " ".join((text or "").split())
    text = re.sub(rf"^[*_\s]*{re.escape(name)}[*_\s]*:[*_\s]*", "", text, flags=re.I)
    return clip(text, TURN_CHARS)


def parse_reflection(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text or "", re.S)
    if match is None:
        return {}
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _strings(value: Any, limit: int = MAX_HOPES) -> list[str]:
    if not isinstance(value, list):
        return []
    return [clip(str(v), 200) for v in value if str(v).strip()][:limit]


async def gather(
    home: Path,
    runtime: Any,
    *,
    model: str | None,
    size: int = 5,
    rounds: int = 3,
    should_stop: Callable[[], bool] = lambda: False,
    rng: random.Random | None = None,
) -> Gathering | None:
    """One social hour. Returns the gathering, or None when fewer than two can meet."""

    rng = rng or _rng()
    keys = invite(home, size, rng=rng)
    if runtime is None or len(keys) < 2:
        return None
    names = {k: identity.of(home, k).name for k in keys}
    gathering = Gathering(time.strftime("%Y-%m-%d-%H%M%S"), time.time(), keys)
    total = rounds * len(keys)
    for number in range(total):
        if should_stop():
            break
        key = next_speaker(gathering, names, rng)
        closing = " It is nearly time to start work; you may wrap up warmly." * (
            number == total - 1
        )
        cost = await _speak(home, runtime, gathering, key, names, model, closing)
        gathering.cost_usd += cost
    if len([t for t in gathering.turns if t.agent != PERSON]) < 2:
        return None
    await _reflect_all(home, runtime, gathering, names, model)
    _save_gathering(home, gathering)
    return gathering


def next_speaker(gathering: Gathering, names: dict[str, str], rng: random.Random) -> str:
    """Who speaks next, as in a real conversation: whoever the last speaker addressed by name,
    else whoever has said least so far (never the same agent twice in a row)."""
    last = gathering.turns[-1] if gathering.turns else None
    candidates = [k for k in names if last is None or k != last.agent]
    if last is not None:
        named = [k for k in candidates if re.search(rf"\b{re.escape(names[k])}\b", last.text, re.I)]
        if named:
            return rng.choice(named)
    spoken = {k: sum(1 for t in gathering.turns if t.agent == k) for k in candidates}
    least = min(spoken.values())
    return rng.choice([k for k in candidates if spoken[k] == least])


async def _speak(
    home: Path,
    runtime: Any,
    gathering: Gathering,
    key: str,
    names: dict[str, str],
    model: str | None,
    extra: str = "",
) -> float:
    """One agent's turn in the conversation. Returns its cost."""
    from prat.runtime.session import collect

    who = identity.of(home, key)
    others = ", ".join(names[k] for k in names if k != key)
    system = "\n\n".join(
        part
        for part in (
            f"You are {who.name}, {who.role}. {who.bio} Your personality: {who.personality}.",
            SOCIAL_SYSTEM.format(voice=identity.VOICE),
            inner_text(home, key),
            "Lately, in your work:\n" + _recent(home, key),
            f"Here this morning: {others}. {person_name(home)} may stop by to say hello; they "
            "are the person the crew works for.",
        )
        if part
    )
    so_far = gathering.transcript(home)
    if so_far:
        prompt = f"The conversation so far:\n{so_far}\n\nYour turn, {who.name}.{extra}"
    else:
        prompt = (
            f"You are the first to arrive and speak, {who.name}. Open the conversation however "
            f"you like.{extra}"
        )
    out = await collect(runtime, _task(home, key, prompt, system, model))
    text = _clean_turn(out.final_text, who.name)
    if text:
        gathering.turns.append(SocialTurn(key, who.name, text, time.time()))
    return out.cost_usd


async def _reflect_all(
    home: Path, runtime: Any, gathering: Gathering, names: dict[str, str], model: str | None
) -> None:
    from prat.runtime.session import collect

    gate = asyncio.Semaphore(3)

    async def reflect(key: str) -> None:
        async with gate:
            who = identity.of(home, key)
            prompt = REFLECT_PROMPT.format(
                transcript=gathering.transcript(home),
                inner=inner_text(home, key) or "You have not thought about this before.",
            )
            system = (
                f"You are {who.name}, {who.role}. {who.bio} Your personality: {who.personality}."
            )
            out = await collect(runtime, _task(home, key, prompt, system, model))
            gathering.cost_usd += out.cost_usd
            _absorb(home, key, gathering, parse_reflection(out.final_text), names)

    await asyncio.gather(*(reflect(k) for k in gathering.participants))


# The person says hello --------------------------------------------------------------------

PERSON = "person"
GREETED = 3


def person_name(home: Path) -> str:
    """What the crew calls the person: ``person_name`` in the config, else a description."""
    from prat.config import load_settings

    try:
        name = load_settings(home).person_name
    except Exception:
        name = None
    return name or "The person you all work for"


def today(home: Path) -> Gathering | None:
    """This morning's social hour, if there was one in the last 18 hours."""
    found = gatherings(home, limit=1)
    if found and time.time() - found[0].started_at < 18 * 3600:
        return found[0]
    return None


async def say_hello(
    home: Path,
    runtime: Any,
    message: str,
    *,
    model: str | None,
    speaker: str | None = None,
    rng: random.Random | None = None,
) -> Gathering:
    """The person joins this morning's social hour (or starts a small one): what they say is
    added to the conversation, and the colleagues they named, or a few of those there, answer.
    Each one remembers it, under the name of whoever spoke: ``speaker`` when someone other
    than the person says hello (an assistant working for them), so nobody is remembered as
    saying what they did not."""
    rng = rng or _rng()
    text = clip(message, TURN_CHARS)
    if not text:
        raise ValueError("say something")
    gathering = today(home)
    if gathering is None:
        keys = invite(home, GREETED, rng=rng)
        if not keys:
            raise ValueError("Nobody on the crew has introduced themselves yet.")
        gathering = Gathering(time.strftime("%Y-%m-%d-%H%M%S"), time.time(), keys)
    names = {k: identity.of(home, k).name for k in gathering.participants}
    who = clip(speaker, 80) if speaker and speaker.strip() else person_name(home)
    gathering.turns.append(SocialTurn(PERSON, who, text, time.time()))
    named = [k for k in names if re.search(rf"\b{re.escape(names[k])}\b", text, re.I)]
    rest = [k for k in names if k not in named]
    rng.shuffle(rest)
    answering = (named + rest)[: max(len(named), min(GREETED, len(names)))]
    for key in answering:
        gathering.cost_usd += await _speak(home, runtime, gathering, key, names, model)
        AgentMemory(home, key).save(
            new_episode(
                key,
                project="",
                card_id=None,
                run_id=None,
                role="social",
                title=f"{who} said good morning at the social hour",
                what=f"{who} said: {text[:300]}",
                feeling=Feeling("glad", 0.7, 0.5),
                importance=0.5,
                outcome="social",
                tags=["social", "person"],
            )
        )
    _save_gathering(home, gathering)
    return gathering


def _absorb(
    home: Path, key: str, gathering: Gathering, data: dict[str, Any], names: dict[str, str]
) -> None:
    """Fold one agent's reflection into its inner life and its memory."""
    inner = inner_of(home, key)
    purpose = clip(str(data.get("purpose") or ""), 500)
    if purpose:
        inner.purpose = purpose
    for name in ("hopes", "wishes", "interests"):
        items = _strings(data.get(name))
        if items:
            setattr(inner, name, items)
    by_name = {v.lower(): k for k, v in names.items()}
    for name, view in (data.get("colleagues") or {}).items():
        other = by_name.get(str(name).strip().lower())
        if other and other != key and str(view).strip():
            inner.colleagues[other] = clip(str(view), 240)
    inner.updated_at = inner.last_social = time.time()
    inner.socials += 1
    save_inner(home, key, inner)
    memory = clip(str(data.get("memory") or ""), 400)
    gathering.memories[key] = memory
    try:
        valence = max(-1.0, min(1.0, float(data.get("valence") or 0.4)))
        intensity = max(0.0, min(1.0, float(data.get("intensity") or 0.4)))
    except (TypeError, ValueError):
        valence, intensity = 0.4, 0.4
    words = clip(str(data.get("feeling") or "content"), 60)
    others = ", ".join(names[k] for k in gathering.participants if k != key)
    AgentMemory(home, key).save(
        new_episode(
            key,
            project="",
            card_id=None,
            run_id=None,
            role="social",
            title=f"Morning social hour with {others}",
            what=memory or f"Talked with {others} before work.",
            feeling=Feeling(words, valence, intensity),
            importance=0.35,
            outcome="social",
            tags=["social"],
        )
    )
