"""The ledger: what kind of work each run was, how it was run and how it went, with nothing
that names whose work it was.

One entry per run, worked out from PRAT's own records (the run, its steps and the card), so the
ledger can be rebuilt at any time and covers runs from before it existed. An entry says:

* **the work**: its type (feature, bug fix, ...), its area (backend API, frontend UI, database,
  ...), its language, the size of the change and the scale of the code base, all in broad terms
  the profession shares. Area, language and size are read from the files the run changed; only
  the type is a guess from the card's wording, and is recorded as one.
* **the settings**: the model and effort of each role, racing, thinking.
* **the outcome**: time in each step, cost, whether PRAT's gates passed first time, the
  reviewer's verdict, who sent the card back (the reviewer or a person) and whether it landed.

It never holds a client, a project's name, a path, a file name, a card's text or any code. A
project and a card are each a random-looking id (a salted hash; the salt stays in this
installation), so runs on one code base can be grouped without saying which it is.

:func:`table` answers the question the ledger exists for: for this kind of work, which
settings finish soonest and still get it right.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select

from prat.db import Database, Run
from prat.domain import CardKind, CardStatus, RunStatus

LEDGER_DIR = "ledger"
SALT_FILE = "salt"
EXPORT_FILE = "runs.jsonl"

# Areas, most specific first: a path belongs to the first whose pattern it matches.
TESTS = "tests"
DOCS = "documentation"
AREAS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        TESTS,
        re.compile(
            r"(^|/)(tests?|__tests__|spec|e2e|cypress|playwright)/"
            r"|(^|/)test_[^/]+$|[._-](test|spec)\.[a-z]+$|_test\.[a-z]+$"
        ),
    ),
    (
        "database",
        re.compile(
            r"(^|/)(migrations?|alembic|schema|seeds?|drizzle|prisma)/|\.sql$|schema\.prisma$"
        ),
    ),
    (
        "infrastructure",
        re.compile(
            r"(^|/)(\.github|\.gitlab|\.circleci|terraform|k8s|helm|deploy|infra|ansible)/"
            r"|(^|/)(Dockerfile[^/]*|docker-compose[^/]*|Jenkinsfile|Procfile)$|\.tf$"
        ),
    ),
    (
        "data engineering",
        re.compile(r"(^|/)(etl|pipelines?|dags?|dbt|airflow|ingest|warehouse)/|\.ipynb$"),
    ),
    (
        "mobile",
        re.compile(
            r"(^|/)(android|ios|mobile|mobile-app|react-native|expo)/"
            r"|\.(swift|kt|kts|dart|m|mm)$|(^|/)app\.json$"
        ),
    ),
    (
        "frontend UI",
        re.compile(
            r"\.(tsx|jsx|vue|svelte|css|scss|sass|less|html|twig|blade\.php|erb|hbs)$"
            r"|(^|/)(components?|pages|views|templates|styles|layouts|screens|web-app|frontend)/"
        ),
    ),
    (DOCS, re.compile(r"\.(md|mdx|rst|adoc|txt)$|(^|/)docs?/")),
    (
        "build and configuration",
        re.compile(
            r"(^|/)(package\.json|pnpm-lock\.yaml|yarn\.lock|package-lock\.json|pyproject\.toml"
            r"|setup\.(py|cfg)|requirements[^/]*\.txt|composer\.(json|lock)|go\.(mod|sum)"
            r"|Cargo\.(toml|lock)|Makefile|tsconfig[^/]*\.json)$|\.(ya?ml|toml|ini|cfg|env)$"
        ),
    ),
    ("command line and tooling", re.compile(r"(^|/)(cli|cmd|bin|scripts?|tools?)/|(^|/)cli[^/]*$")),
    (
        "backend API",
        re.compile(
            r"(^|/)(api|routes?|routers?|controllers?|handlers?|endpoints?|resolvers?|services?"
            r"|server|backend|middleware)/"
        ),
    ),
)
# A source file in no named area.
OTHER_CODE = "application code"

LANGUAGES: Mapping[str, str] = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".vue": "JavaScript",
    ".svelte": "JavaScript",
    ".php": "PHP",
    ".go": "Go",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".cs": "C#",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".sql": "SQL",
    ".sh": "Shell",
    ".dart": "Dart",
}

# Signs in the lines a change adds that a screen responds to its user and moves: state, events
# and animation, in the common web and mobile libraries.
INTERACTIVE = re.compile(
    r"\b(useState|useReducer|useEffect|useRef|onClick|onPress|onChange|onSubmit|onDrag\w*"
    r"|addEventListener|v-model|@click|animate\w*|transition|keyframes|framer-motion"
    r"|Animated\.|useSharedValue|withTiming|withSpring|gesture\w*|draggable)\b"
)
INTERACTIVE_AT_LEAST = 5

# The type of work, from the card's own words: the first that matches. A guess, and stored as one.
WORK_TYPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "bug fix",
        re.compile(
            r"\b(fix(es|ed)?|bug|broken|crash(es)?|regression|wrong|incorrect|fails?|error)\b", re.I
        ),
    ),
    (
        "refactor",
        re.compile(r"\b(refactor\w*|clean ?up|rename|restructure|simplif\w+|extract)\b", re.I),
    ),
    (
        "performance",
        re.compile(r"\b(performance|faster|slow|speed up|optimi[sz]e\w*|cache)\b", re.I),
    ),
    (
        "schema or data migration",
        re.compile(r"\b(migration|migrate|backfill|schema change|new column|new table)\b", re.I),
    ),
    ("tests", re.compile(r"^\s*(add|write|extend)\b.*\btests?\b|\btest coverage\b", re.I)),
    ("documentation", re.compile(r"^\s*(document|docs?:)|\b(readme|documentation)\b", re.I)),
    (
        "build and configuration",
        re.compile(r"\b(upgrade|bump|dependenc\w+|ci\b|pipeline|configure|configuration)\b", re.I),
    ),
)
FEATURE = "feature"


def area_of(path: str) -> str:
    text = path.replace("\\", "/")
    for name, pattern in AREAS:
        if pattern.search(text):
            return name
    return OTHER_CODE if PurePosixPath(text).suffix.lower() in LANGUAGES else "other"


def bucket(value: int, steps: Sequence[tuple[int, str]], top: str) -> str:
    for limit, name in steps:
        if value <= limit:
            return name
    return top


def change_size(lines: int) -> str:
    return bucket(
        lines,
        ((20, "tiny (up to 20 lines)"), (100, "small (21 to 100)"), (400, "medium (101 to 400)")),
        "large (over 400)" if lines <= 1500 else "very large (over 1,500)",
    )


def codebase_scale(files: int | None) -> str | None:
    if files is None:
        return None
    return bucket(
        files,
        ((200, "small (up to 200 files)"), (2000, "medium (201 to 2,000)")),
        "large (2,001 to 20,000)" if files <= 20000 else "very large (over 20,000)",
    )


@dataclass
class Work:
    """What a change was, read from the files it touched."""

    area: str = "none"
    areas: dict[str, int] = field(default_factory=dict)
    language: str | None = None
    lines: int = 0
    files: int = 0
    with_tests: bool = False


def work_of(files: Iterable[Mapping[str, Any]]) -> Work:
    """The area and language holding most of the changed lines. Tests and documentation count
    as the area only when nothing else changed: a feature with its tests is not "tests"."""
    by_area: Counter[str] = Counter()
    by_language: Counter[str] = Counter()
    lines = count = 0
    for item in files:
        path = str(item.get("path") or "")
        changed = int(item.get("added") or 0) + int(item.get("deleted") or 0)
        count += 1
        lines += changed
        by_area[area_of(path)] += max(changed, 1)
        language = LANGUAGES.get(PurePosixPath(path.replace("\\", "/")).suffix.lower())
        if language:
            by_language[language] += max(changed, 1)
    main = [(n, a) for a, n in by_area.items() if a not in (TESTS, DOCS, "other")]
    ranked = sorted(main or [(n, a) for a, n in by_area.items()], reverse=True)
    return Work(
        area=ranked[0][1] if ranked else "none",
        areas=dict(by_area),
        language=by_language.most_common(1)[0][0] if by_language else None,
        lines=lines,
        files=count,
        with_tests=TESTS in by_area,
    )


def work_type(title: str) -> str:
    """A guess from the card's title alone: a goal names errors and fixes whatever the work."""
    for text in (title,):
        for name, pattern in WORK_TYPES:
            if pattern.search(text or ""):
                return name
    return FEATURE


def spec_detail(goal: str, criteria: str) -> str:
    words = len((goal or "").split())
    size = bucket(words, ((40, "brief"), (250, "described")), "specified in full")
    return size + (", with acceptance criteria" if (criteria or "").strip() else "")


class Anonymiser:
    """Stable ids that cannot be turned back into a project or a card."""

    def __init__(self, home: Path) -> None:
        folder = home / LEDGER_DIR
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / SALT_FILE
        if not path.exists():
            path.write_text(secrets.token_hex(16), encoding="utf-8")
        self._salt = path.read_text(encoding="utf-8").strip()

    def __call__(self, kind: str, value: object) -> str:
        return hashlib.sha256(f"{self._salt}:{kind}:{value}".encode()).hexdigest()[:12]


@dataclass
class Entry:
    run: str
    card: str
    codebase: str
    month: str
    # The work.
    work_type: str
    area: str
    language: str | None
    change_size: str
    lines_changed: int
    files_changed: int
    with_tests: bool
    interactive_ui: bool | None
    codebase_scale: str | None
    complexity: str
    spec: str
    part_of_epic: bool
    # The settings.
    models: dict[str, str]
    efforts: dict[str, str]
    racing: int | None
    thinking: bool | None
    fact_pack: bool | None
    # The outcome.
    attempt: int
    sent_back_by: str | None
    status: str
    seconds: float | None
    step_seconds: dict[str, float]
    cost_usd: float
    gates_passed: bool | None
    finish_checks: int
    verdict: str | None
    landed: bool

    @property
    def right(self) -> bool:
        """The run produced work that passed PRAT's gates and its reviewer."""
        return self.status == str(RunStatus.SUCCEEDED) and self.verdict in ("met", None)


def _detail(run: Run, step: str) -> dict[str, Any]:
    found = next((s for s in run.steps if str(s.name) == step), None)
    return dict(found.detail or {}) if found is not None else {}


def entry(
    run: Run,
    anon: Callable[[str, object], str],
    *,
    earlier: Run | None = None,
    scale: Callable[[Run], int | None] | None = None,
    added_lines: Callable[[Run], str | None] | None = None,
) -> Entry:
    card = run.card
    execute, verify, critique = (_detail(run, s) for s in ("execute", "verify", "critique"))
    summary = _detail(run, "summarize")
    files = summary.get("files") or (verify.get("changes") or {}).get("files") or []
    work = work_of(f for f in files if isinstance(f, Mapping))
    config = execute.get("config") or {}
    models = {
        role: max(usage.get("models") or {"": {}}, key=lambda m: (usage["models"][m]).get("usd", 0))
        for role, usage in ((run.usage or {}).get("roles") or {}).items()
        if usage.get("models")
    }
    gates = verify.get("gates")
    interactive = None
    if work.area in ("frontend UI", "mobile") and added_lines is not None:
        text = added_lines(run)
        interactive = (
            None if text is None else len(INTERACTIVE.findall(text)) >= INTERACTIVE_AT_LEAST
        )
    sent_back = None
    if earlier is not None:
        sent_back = "the reviewer" if _detail(earlier, "critique").get("rework") else "a person"
        if earlier.status != RunStatus.SUCCEEDED:
            sent_back = "a failed run"
    seconds = (
        (run.finished_at - run.started_at).total_seconds()
        if run.started_at and run.finished_at
        else None
    )
    return Entry(
        run=anon("run", run.id),
        card=anon("card", card.id),
        codebase=anon("project", card.project_id),
        month=(run.created_at.strftime("%Y-%m") if run.created_at else ""),
        work_type=work_type(card.title),
        area=work.area,
        language=work.language,
        change_size=change_size(work.lines),
        lines_changed=work.lines,
        files_changed=work.files,
        with_tests=work.with_tests,
        interactive_ui=interactive,
        codebase_scale=codebase_scale(scale(run) if scale is not None else None),
        complexity=str(card.complexity),
        spec=spec_detail(card.goal, card.acceptance_criteria),
        part_of_epic=card.parent_id is not None,
        models=models,
        efforts=dict(config.get("efforts") or {}),
        racing=config.get("race_builders"),
        thinking=config.get("thinking"),
        fact_pack=config.get("fact_pack"),
        attempt=run.attempt,
        sent_back_by=sent_back,
        status=str(run.status),
        seconds=round(seconds, 1) if seconds is not None else None,
        step_seconds={
            str(s.name): round((s.finished_at - s.started_at).total_seconds(), 1)
            for s in run.steps
            if s.started_at and s.finished_at
        },
        cost_usd=round(
            (run.cost_usd or 0.0) + float(critique.get("cost_usd") or 0.0),
            4,
        ),
        gates_passed=all(g.get("passed") for g in gates) if isinstance(gates, list) else None,
        finish_checks=int(execute.get("finish_checks") or 0),
        verdict=critique.get("verdict"),
        landed=card.status == CardStatus.DONE,
    )


def build(
    db: Database,
    home: Path,
    *,
    scale: Callable[[Run], int | None] | None = None,
    added_lines: Callable[[Run], str | None] | None = None,
) -> list[Entry]:
    """An entry for every finished run of a change card, oldest first."""
    anon = Anonymiser(home)
    entries: list[Entry] = []
    with db.session() as session:
        previous: dict[int, Run] = {}
        for run in session.scalars(select(Run).order_by(Run.id)):
            if run.finished_at is None or run.card.kind is not CardKind.CHANGE:
                continue
            entries.append(
                entry(
                    run,
                    anon,
                    earlier=previous.get(run.card_id),
                    scale=scale,
                    added_lines=added_lines,
                )
            )
            previous[run.card_id] = run
    return entries


def export(entries: Sequence[Entry], home: Path) -> Path:
    path = home / LEDGER_DIR / EXPORT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(asdict(e), sort_keys=True) + "\n" for e in entries), encoding="utf-8"
    )
    return path


def load(paths: Iterable[Path]) -> list[Entry]:
    """Entries from exported files (this machine's and any copied from another), each run once."""
    seen: dict[str, Entry] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = Entry(**json.loads(line))
                seen[item.run] = item
    return list(seen.values())


def setting(item: Entry) -> str:
    """The builder's model and effort, the way a row of the table names a setting."""
    model = item.models.get("builder") or "unknown model"
    effort = item.efforts.get("builder")
    return f"{model}, {effort} effort" if effort else model


@dataclass
class Row:
    work: tuple[str, ...]
    setting: str
    runs: int
    right: int
    seconds: float | None
    cost_usd: float
    sent_back_by_person: int

    @property
    def right_share(self) -> float:
        return self.right / self.runs if self.runs else 0.0


def table(entries: Iterable[Entry], by: Sequence[str] = ("area", "work_type")) -> list[Row]:
    """For each kind of work and each setting: how many runs, how many were right, the median
    time of those that were, the median cost, and how many a person had to send back. Within a
    kind of work the settings come best first: most often right, then soonest."""
    groups: dict[tuple[tuple[str, ...], str], list[Entry]] = defaultdict(list)
    for item in entries:
        key = tuple(str(getattr(item, name)) for name in by)
        groups[(key, setting(item))].append(item)
    rows = []
    for (work, name), items in groups.items():
        good = [i.seconds for i in items if i.right and i.seconds is not None]
        rows.append(
            Row(
                work=work,
                setting=name,
                runs=len(items),
                right=sum(1 for i in items if i.right),
                seconds=round(statistics.median(good), 1) if good else None,
                cost_usd=round(statistics.median(i.cost_usd for i in items), 4),
                sent_back_by_person=sum(1 for i in items if i.sent_back_by == "a person"),
            )
        )
    rows.sort(key=lambda r: (r.work, -r.right_share, r.seconds if r.seconds is not None else 1e9))
    return rows
