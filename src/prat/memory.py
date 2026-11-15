"""Agent memory: each agent remembers what it did, how it reasoned and how it felt.

Every agent in PRAT (each crew lead, each helper, the planner and the reviewers) has a memory of
its own under ``$PRAT_HOME/agents/<agent>/``, kept the way people keep theirs:

* **Short-term (working) memory**, per card: the notes, thoughts and feelings the agent writes
  while it works (the ``remember`` tool), and the reasoning it thought aloud. It lasts across
  the card's runs and reworks, and is folded into an episode when the card is finished.
* **Episodes (long-term memory)**: one per piece of work. What the card was, what the agent
  did, how it reasoned, how it felt about it, what it learned, and how it turned out (landed,
  sent back with what feedback, failed). Outcomes arrive later and change the episode, and the
  feeling: a card sent back is remembered with the disappointment, and more strongly.
* **Beliefs**: lessons distilled from many episodes during a dream (``consolidate``), each
  with the episodes that taught it and a strength that grows when it is confirmed.

At the start of a session the agent is given what it most needs to remember for this card:
its beliefs about this project and in general, the most relevant episodes, and its working
memory of this card. During the session it can ``recall`` anything it has ever done. Recall
weighs how relevant a memory is to the question, how important it was, how strongly it was
felt (strong feelings make memories last, as they do for people) and how recent it is.

Everything is plain JSON files, private to this installation.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

AGENTS_DIR = "agents"
HALF_LIFE_DAYS = 45.0
WORKING_LIMIT = 60
THOUGHT_CHARS = 600
_WORD = re.compile(r"[a-z0-9_]{3,}")
_STOP = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "have",
        "has",
        "was",
        "were",
        "are",
        "not",
        "but",
        "you",
        "your",
        "its",
        "it's",
        "their",
        "there",
        "what",
        "when",
        "then",
        "than",
        "them",
        "they",
        "will",
        "would",
        "should",
        "could",
        "about",
        "which",
        "while",
        "card",
        "agent",
        "code",
        "file",
        "files",
    ]
)
_lock = threading.Lock()


@dataclass
class Feeling:
    """How the agent felt: a word or two, valence from -1 (bad) to 1 (good), intensity 0 to 1."""

    words: str = "neutral"
    valence: float = 0.0
    intensity: float = 0.2


@dataclass
class Episode:
    id: str
    at: float
    agent: str
    project: str
    card_id: int | None
    run_id: int | None
    role: str
    title: str
    what: str = ""
    reasoning: str = ""
    lesson: str = ""
    # What the agent resolved to do better next time.
    intention: str = ""
    feeling: Feeling = field(default_factory=Feeling)
    outcome: str = "pending"  # pending, landed, reworked, failed, blocked, cancelled
    feedback: str = ""
    importance: float = 0.5
    tags: list[str] = field(default_factory=list)
    recalled: int = 0
    # Taken from PRAT's record of work done before the agent kept memories (prat.inherit):
    # what happened is true, but the agent did not live it and its feeling is the outcome's.
    inherited: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Episode:
        values = dict(data)
        values["feeling"] = Feeling(**(values.get("feeling") or {}))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in values.items() if k in known})

    def text(self) -> str:
        return " ".join(
            [self.title, self.what, self.reasoning, self.lesson, self.feedback, " ".join(self.tags)]
        )


@dataclass
class Belief:
    id: str
    text: str
    project: str | None = None
    strength: float = 0.5
    evidence: list[str] = field(default_factory=list)
    formed_at: float = field(default_factory=time.time)
    confirmed_at: float = field(default_factory=time.time)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Belief:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Note:
    """A line of working memory: a note, a thought, a feeling or a plan."""

    at: float
    kind: str
    text: str
    feeling: Feeling | None = None


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def _safe(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or "agent"


class AgentMemory:
    """One agent's memory on disk."""

    def __init__(self, home: Path, agent: str) -> None:
        self.agent = _safe(agent)
        self.root = home / AGENTS_DIR / self.agent
        self.episodes_path = self.root / "episodes.jsonl"
        self.beliefs_path = self.root / "beliefs.json"
        self.working_dir = self.root / "working"

    # Reading --------------------------------------------------------------------------

    def episodes(self) -> list[Episode]:
        try:
            lines = self.episodes_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        found: dict[str, Episode] = {}
        for line in lines:
            try:
                episode = Episode.from_dict(json.loads(line))
            except (ValueError, TypeError):
                continue
            found[episode.id] = episode  # a later line for the same id is its update
        return list(found.values())

    def beliefs(self) -> list[Belief]:
        try:
            data = json.loads(self.beliefs_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [Belief.from_dict(item) for item in data if isinstance(item, Mapping)]

    def working(self, project: str, card_id: int) -> list[Note]:
        try:
            data = json.loads(self._working_path(project, card_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        notes = []
        for item in data if isinstance(data, list) else []:
            feeling = item.get("feeling")
            notes.append(
                Note(
                    float(item.get("at") or 0),
                    str(item.get("kind") or "note"),
                    str(item.get("text") or ""),
                    Feeling(**feeling) if isinstance(feeling, Mapping) else None,
                )
            )
        return notes

    # Writing ----------------------------------------------------------------------------

    def _working_path(self, project: str, card_id: int) -> Path:
        return self.working_dir / f"{_safe(project)}-{card_id}.json"

    def note(
        self, project: str, card_id: int, kind: str, text: str, feeling: Feeling | None = None
    ) -> None:
        """Add a line to the card's working memory."""
        text = " ".join(text.split())[:THOUGHT_CHARS]
        if not text:
            return
        with _lock:
            notes = self.working(project, card_id)
            notes.append(Note(time.time(), kind, text, feeling))
            notes = notes[-WORKING_LIMIT:]
            path = self._working_path(project, card_id)
            _write_json(path, [_note_dict(n) for n in notes])

    def save(self, episode: Episode) -> None:
        """Add an episode, or record a change to one (the latest line wins)."""
        with _lock:
            self.root.mkdir(parents=True, exist_ok=True)
            with self.episodes_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(episode)) + "\n")

    def save_beliefs(self, beliefs: list[Belief]) -> None:
        with _lock:
            _write_json(self.beliefs_path, [asdict(b) for b in beliefs])

    def compact(self) -> None:
        """Rewrite the episode file with one line per episode."""
        episodes = self.episodes()
        with _lock:
            _write_lines(self.episodes_path, [json.dumps(asdict(e)) for e in episodes])

    def forget_working(self, project: str, card_id: int) -> None:
        self._working_path(project, card_id).unlink(missing_ok=True)

    # Recall -------------------------------------------------------------------------------

    def recall(
        self, query: str, *, project: str | None = None, limit: int = 5, now: float | None = None
    ) -> list[Episode]:
        """The episodes that best answer ``query``: relevance, importance, feeling, recency."""
        episodes = self.episodes()
        if not episodes:
            return []
        moment = now or time.time()
        wanted = Counter(_words(query))
        documents = [Counter(_words(e.text())) for e in episodes]
        seen = Counter(w for doc in documents for w in doc)
        total = len(documents)
        scored = []
        for episode, words in zip(episodes, documents, strict=True):
            relevance = sum(
                min(count, words[w]) * math.log(1 + total / seen[w])
                for w, count in wanted.items()
                if w in words
            )
            if wanted and relevance == 0:
                continue
            age_days = max(moment - episode.at, 0) / 86400
            recency = 0.5 ** (age_days / HALF_LIFE_DAYS)
            felt = abs(episode.feeling.valence) * episode.feeling.intensity
            weight = (1 + episode.importance + felt) * (0.4 + 0.6 * recency)
            if project and episode.project == project:
                weight *= 1.5
            scored.append(((relevance or 1.0) * weight, episode))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [episode for _, episode in scored[:limit]]

    def mark_recalled(self, episodes: Iterable[Episode]) -> None:
        """Recalling a memory makes it a little stronger, as for people."""
        for episode in episodes:
            episode.recalled += 1
            episode.importance = min(1.0, episode.importance + 0.02)
            self.save(episode)

    def stats(self) -> dict[str, Any]:
        episodes = self.episodes()
        beliefs = self.beliefs()
        outcomes = Counter(e.outcome for e in episodes)
        mood = [e.feeling.valence for e in episodes[-10:]]
        return {
            "agent": self.agent,
            "episodes": len(episodes),
            "beliefs": len(beliefs),
            "outcomes": dict(outcomes),
            "mood": round(sum(mood) / len(mood), 2) if mood else 0.0,
            "last": episodes[-1].at if episodes else None,
        }


# Briefing an agent --------------------------------------------------------------------


def track_record(memory: AgentMemory, *, reader: str = "agent") -> str:
    """The agent's own record, so it knows where it stands and what it means to improve: cards
    landed, landed on the first review, sent back, its current streak and its resolutions.
    ``reader="person"`` words it for the person reading about the agent."""
    work = [
        e
        for e in memory.episodes()
        if e.role not in ("conversation", "helper")
        and e.outcome in ("landed", "reworked", "failed")
    ]
    if not work:
        return ""
    landed = sum(1 for e in work if e.outcome == "landed")
    first = sum(1 for e in work if e.outcome == "landed" and "proud" in e.feeling.words)
    sent_back = sum(1 for e in work if e.outcome == "reworked")
    streak = 0
    for episode in reversed(work):
        if episode.outcome != "landed":
            break
        streak += 1
    person = reader == "person"
    failed = sum(1 for e in work if e.outcome == "failed")
    lines = [
        f"{'Record' if person else 'Your record'}: {landed} cards landed ({first} on the first "
        f"review), {sent_back} sent back, {failed} failed."
    ]
    if streak > 1:
        lines[0] += (
            f" {streak} landed in a row."
            if person
            else f" You have landed {streak} in a row; keep it going."
        )
    intentions = [e.intention for e in memory.episodes() if e.intention][-3:]
    if intentions:
        lines.append(
            "They resolved, after earlier work:" if person else "You resolved, after earlier work:"
        )
        lines.extend(f"- {text}" for text in intentions)
    return "\n".join(lines)


def briefing(
    memory: AgentMemory,
    *,
    project: str,
    card_id: int | None,
    query: str,
    budget: int = 3500,
    episodes: int = 4,
    name: str | None = None,
) -> str:
    """What the agent should remember at the start of a session, within ``budget`` characters."""
    parts: list[str] = []
    record = track_record(memory)
    if record:
        parts.append(record)
    beliefs = sorted(
        (b for b in memory.beliefs() if b.project in (None, project)),
        key=lambda b: b.strength,
        reverse=True,
    )[:8]
    if beliefs:
        parts.append("What you believe from experience:")
        parts.extend(f"- {b.text}" for b in beliefs)
    recalled = memory.recall(query, project=project, limit=episodes)
    recalled = [e for e in recalled if e.card_id != card_id or e.project != project]
    if recalled:
        parts.append("Past work you remember that is relevant now:")
        for episode in recalled:
            when = time.strftime("%Y-%m-%d", time.localtime(episode.at))
            line = (
                f"- {when}, {episode.project} #{episode.card_id}: {episode.title}. {episode.what}"
            )
            if episode.inherited:
                line = line.replace("- ", "- (from the record, before you kept memories) ", 1)
            if episode.outcome != "pending":
                line += f" Outcome: {episode.outcome}"
                if episode.feedback:
                    line += f" ({episode.feedback})"
                line += "."
            if episode.lesson:
                line += f" You learned: {episode.lesson}"
            if episode.feeling.words != "neutral" and not episode.inherited:
                line += f" You felt {episode.feeling.words}."
            parts.append(" ".join(line.split()))
        memory.mark_recalled(recalled)
    if card_id is not None:
        notes = memory.working(project, card_id)
        if notes:
            parts.append("Your working memory of this card (from earlier runs):")
            for note in notes[-12:]:
                parts.append(f"- [{note.kind}] {note.text}")
    if not parts:
        return ""
    text = "\n".join(
        [
            "# Your memory",
            f"You are {name or memory.agent}. These are your own memories of past work.",
            *parts,
            "Use recall to search everything you have ever done, and remember to keep notes, "
            "thoughts and feelings as you work. Before you finish, reflect: what you did, how "
            "you reasoned, how you feel about it and what you learned.",
        ]
    )
    return text[:budget]


# Outcomes --------------------------------------------------------------------------------

OUTCOME_FEELINGS = {
    "landed": Feeling("satisfied", 0.7, 0.5),
    "landed_first": Feeling("proud", 0.9, 0.7),
    "reworked": Feeling("disappointed", -0.6, 0.7),
    "failed": Feeling("frustrated", -0.7, 0.6),
    "blocked": Feeling("uncertain", -0.2, 0.4),
    "cancelled": Feeling("indifferent", 0.0, 0.1),
}


def settle(
    home: Path,
    *,
    project: str,
    card_id: int,
    outcome: str,
    feedback: str = "",
    first_review: bool = False,
) -> int:
    """Tell every agent that worked on a card how it turned out. Returns episodes changed."""
    changed = 0
    root = home / AGENTS_DIR
    if not root.is_dir():
        return 0
    key = "landed_first" if outcome == "landed" and first_review else outcome
    felt = OUTCOME_FEELINGS.get(key, Feeling())
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        memory = AgentMemory(home, folder.name)
        for episode in memory.episodes():
            if episode.project != project or episode.card_id != card_id:
                continue
            if episode.outcome == outcome and episode.feedback == feedback:
                continue
            apply_outcome(episode, outcome, felt, feedback)
            memory.save(episode)
            changed += 1
    return changed


def apply_outcome(episode: Episode, outcome: str, felt: Feeling, feedback: str = "") -> None:
    """Record how the work turned out. The outcome's feeling blends with the agent's own, and
    mistakes are remembered more."""
    episode.outcome = outcome
    if feedback:
        episode.feedback = clip(feedback, 600)
    episode.feeling = Feeling(
        felt.words
        if episode.feeling.words == "neutral"
        else f"{episode.feeling.words}, then {felt.words}",
        round((episode.feeling.valence + felt.valence) / 2, 2),
        round(max(episode.feeling.intensity, felt.intensity), 2),
    )
    if outcome in ("reworked", "failed"):
        episode.importance = min(1.0, episode.importance + 0.2)


def clip(text: str, limit: int) -> str:
    """At most ``limit`` characters, ending at a whole sentence when it must be shortened."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > 0 else cut.rsplit(" ", 1)[0] + "."


def new_episode(
    agent: str,
    *,
    project: str,
    card_id: int | None,
    run_id: int | None,
    role: str,
    title: str,
    **values: Any,
) -> Episode:
    return Episode(
        id=uuid.uuid4().hex[:12],
        at=time.time(),
        agent=_safe(agent),
        project=project,
        card_id=card_id,
        run_id=run_id,
        role=role,
        title=title,
        **values,
    )


def all_agents(home: Path) -> list[str]:
    root = home / AGENTS_DIR
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


# Files ---------------------------------------------------------------------------------


def _note_dict(note: Note) -> dict[str, Any]:
    data: dict[str, Any] = {"at": note.at, "kind": note.kind, "text": note.text}
    if note.feeling is not None:
        data["feeling"] = asdict(note.feeling)
    return data


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    os.replace(tmp, path)
