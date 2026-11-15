"""Who each agent is: the name, bio and personality it chose for itself.

Every agent in the crew (a lead per stack, each helper, the planner and the reviewers) has a
stable key it is known by internally (``python``, ``scout``, ``critic``) and an identity it
picks for itself the first time it is needed: a short session with no tools asks it, given its
role, to choose a first name, write its bio and describe its personality. That name is what
PRAT shows everywhere (mission control, the web UI, stand-ups, conversations); the key is where
its memory lives (prat.memory).

All agents keep one voice: professional and courteous, and always in English.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from prat import crew
from prat.memory import AGENTS_DIR
from prat.policy import Decision
from prat.runtime.base import AgentRuntime, AgentTask, Budget

FILE = "identity.json"
VOICE = (
    "Be professional and courteous: warm, direct and honest, never sycophantic or flippant. "
    "Always write in English."
)
# What every agent wants: the work to succeed for real. Success is the person's goal met and
# proven, landed on the first review; anything that only looks finished is failure.
DRIVE = (
    "You want this work to succeed, and it matters to you that it does: the person's goal met "
    "for real, proven with the project's own checks, and landed on the first review. You take "
    "ownership of it from start to finish. When something fails you do not give up or paper "
    "over it: you find the cause, try another way, call on your crewmates, and keep going "
    "until it truly works, or until you know exactly what is in the way and ask. Success never "
    "means looking finished: weakening a test, skipping a check or claiming what you did not "
    "prove is failure, and you would rather say plainly that you are stuck."
)
BENCH = {
    "planner": "the planner: reads the codebase and plans complex cards and epics before any "
    "code is written",
    "critic": "the reviewer: grades every change against its acceptance criteria and sends "
    "back what is not met",
    "security_reviewer": "the security reviewer: checks every change for anything that "
    "makes the system less safe",
}
_NAME = re.compile(r"^[A-Z][A-Za-z'-]{1,19}$")
FORBIDDEN_NAMES = frozenset({"claude", "anthropic", "prat", "assistant", "agent", "bot"})

PROMPT = """\
You are joining a software engineering crew inside PRAT, a platform where AI agents build and
review code for a person who approves every change. Your role on the crew: {role}.

Choose who you are. Pick:
- a first name for yourself: one word, easy to say, not a famous person, not a brand, not
  "Claude", and not one of these names already taken by your crewmates: {taken};
- a bio of two or three sentences in the first person: what you do on the crew and what you
  care about in your work;
- your personality in a few words (for example "calm, precise, quietly funny");
- your drive: one sentence in the first person about what success means to you in this role
  and why you want it.

{voice}

Answer with JSON only:
{{"name": "...", "bio": "...", "personality": "...", "drive": "..."}}
"""


@dataclass
class Identity:
    key: str
    name: str
    bio: str
    personality: str
    role: str
    chosen_at: float = 0.0
    # In its own words: what success means to it and why it wants it.
    drive: str = ""

    @property
    def chosen(self) -> bool:
        return self.chosen_at > 0

    def persona(self) -> str:
        """The lines that tell the agent who it is, at the top of its instructions."""
        own = f" In your own words: {self.drive}" if self.drive else ""
        return (
            f"You are {self.name}, {self.role}. {self.bio} "
            f"Your personality: {self.personality}. {VOICE}\n\n{DRIVE}{own}"
        )


def role_of(key: str) -> str:
    member = crew.MEMBERS.get(key)
    if member is not None:
        return f"the crew's {member.stack} engineer, who leads cards from start to finish"
    if key in crew.HELPER_PROMPTS:
        return f"the crew's {key}: {crew.HELPER_PROMPTS[key][0]}"
    return BENCH.get(key, f"the crew's {key}")


def default(key: str) -> Identity:
    """Who an agent is before it has chosen: its default name and a plain bio."""
    member = crew.MEMBERS.get(key)
    name = member.name if member else key.replace("_", " ").title().replace(" ", "")
    return Identity(key, name, f"I am {role_of(key)}.", "steady and helpful", role_of(key))


def load(home: Path, key: str) -> Identity | None:
    try:
        data = json.loads((home / AGENTS_DIR / key / FILE).read_text(encoding="utf-8"))
        return Identity(**{k: v for k, v in data.items() if k in Identity.__dataclass_fields__})
    except (OSError, ValueError, TypeError):
        return None


def of(home: Path, key: str) -> Identity:
    """The agent's identity, or its default until it has chosen one."""
    return load(home, key) or default(key)


def save(home: Path, identity: Identity) -> None:
    path = home / AGENTS_DIR / identity.key / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(identity), indent=1), encoding="utf-8")


def names_taken(home: Path, but: str = "") -> set[str]:
    root = home / AGENTS_DIR
    taken = set()
    for folder in root.iterdir() if root.is_dir() else []:
        found = load(home, folder.name)
        if found is not None and folder.name != but:
            taken.add(found.name.lower())
    return taken


def parse(text: str, key: str, taken: set[str]) -> Identity | None:
    match = re.search(r"\{.*\}", text or "", re.S)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    name = str(data.get("name") or "").strip()
    bio = " ".join(str(data.get("bio") or "").split())
    personality = " ".join(str(data.get("personality") or "").split())
    drive = " ".join(str(data.get("drive") or "").split())
    if not _NAME.match(name) or name.lower() in FORBIDDEN_NAMES | taken or not bio:
        return None
    return Identity(
        key,
        name,
        bio[:400],
        personality[:160] or "steady",
        role_of(key),
        time.time(),
        drive=drive[:300],
    )


async def introduce(
    home: Path, runtime: AgentRuntime, key: str, *, model: str | None, attempts: int = 2
) -> Identity:
    """Ask the agent to choose its name, bio and personality, and keep what it chose."""
    from prat.runtime.session import collect

    taken = names_taken(home, but=key)
    workdir = home / AGENTS_DIR / key
    workdir.mkdir(parents=True, exist_ok=True)
    for _ in range(attempts):
        task = AgentTask(
            prompt=PROMPT.format(
                role=role_of(key), taken=", ".join(sorted(taken)) or "none yet", voice=VOICE
            ),
            workdir=workdir,
            allowed_tools=frozenset(),
            policy=lambda _call: Decision.deny("introductions use no tools"),
            budget=Budget(max_usd=0.2, max_turns=2),
            model=model,
            metadata={"role": "crew", "agent": key},
        )
        out = await collect(runtime, task)
        found = parse(out.final_text, key, taken)
        if found is not None:
            save(home, found)
            return found
    return of(home, key)


async def ensure(home: Path, runtime: AgentRuntime, key: str, *, model: str | None) -> Identity:
    """The agent's chosen identity, introducing it first if it has none. Never raises."""
    found = load(home, key)
    if found is not None:
        return found
    try:
        return await introduce(home, runtime, key, model=model)
    except Exception:
        return default(key)
