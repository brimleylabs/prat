"""Seed a throwaway PRAT installation with sample cards and serve the web UI.

Used to look at the UI and to regenerate the screenshots in docs/images.
Everything runs on the fake runtime: no model, no API key, no cost.

    python scripts/ui_demo.py <empty-directory> [--port 18300]

Run it again on the same directory to serve the same data again.

The directory gets two sample repositories, their agent clones and a PRAT
home. Cards are run through the real engine (worktrees, gates, merge) with
scripted agents, so every status on the board is real. One card keeps
running while the server is up, to show the live session. Print a login link
for any page with:

    python -m prat --home <directory>/home login-url --next /board
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from prat import rhythm, store
from prat.config import Settings, load_settings
from prat.db import Database
from prat.db.migrate import upgrade
from prat.domain import CardStatus, CommentAuthor, Complexity, Verdict
from prat.dream import run_dream
from prat.engine import CriterionFinding, Engine, ReviewRequest, ReviewResult
from prat.knowledge.entries import Entry, KnowledgeBase, new_id
from prat.projects import load_spec
from prat.runtime import AgentTask, FakeRuntime, FakeScript
from prat.web import create_app
from prat.web.tokens import ensure_access_token, login_url


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_repo(root: Path, name: str, files: dict[str, str], toml: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Sample Author")
    git(repo, "config", "user.email", "author@example.invalid")
    git(repo, "config", "core.autocrlf", "false")
    for rel, content in {**files, ".prat/project.toml": toml}.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Initial commit")
    return repo


TEXTKIT_TOML = """\
name = "textkit"
default_branch = "main"
autonomy = "medium"
protected_paths = ["docs/release/**"]
notes = "Small text utilities. Pure functions in textkit/, one test module per module in tests/."

[commands]
test = ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:asyncio"]
lint = ["python", "-m", "compileall", "-q", "textkit"]

[verify]
gates = ["test", "lint"]
timeout_seconds = 120
"""

INVENTORY_TOML = """\
name = "inventory-api"
default_branch = "main"
autonomy = "low"
protected_paths = ["migrations/**"]
notes = "HTTP API for stock levels. Handlers in inventory/handlers.py."

[commands]
test = ["python", "-c", "print('12 passed')"]

[verify]
gates = ["test"]
"""

TEXTKIT_FILES = {
    "README.md": "# textkit\n\nSmall, dependency-free text helpers.\n",
    "textkit/__init__.py": '"""Small text helpers."""\n',
    "tests/test_package.py": "import textkit\n\n\ndef test_imports():\n    assert textkit\n",
}
INVENTORY_FILES = {
    "README.md": "# inventory-api\n",
    "inventory/__init__.py": "",
    "inventory/handlers.py": (
        "ITEMS = [{'sku': f'SKU-{n:04d}', 'qty': n % 7} for n in range(250)]\n\n\n"
        "def list_items():\n    return ITEMS\n"
    ),
}

SLUG = '''import re
import unicodedata


def slugify(text: str) -> str:
    """Lowercase ASCII words joined by dashes."""
    normal = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", normal.lower()).strip("-")
'''
COUNT = '''import re

_WORD = re.compile(r"\\w+(?:[-']\\w+)*", re.UNICODE)


def word_count(text: str) -> int:
    """Count words, treating hyphenated and apostrophe words as one."""
    return len(_WORD.findall(text))
'''
COUNT_TESTS = """from textkit.count import word_count


def test_ascii():
    assert word_count("one two  three") == 3


def test_unicode_and_joined_words():
    assert word_count("caf\\u00e9 na\\u00efve don't well-known") == 4


def test_empty():
    assert word_count("") == 0
"""


def scripts() -> dict[str, list[dict[str, Any]]]:
    return {
        "slugify": [
            {"op": "text", "text": "I'll add slugify() in a new module with tests."},
            {"op": "read", "path": "textkit/__init__.py"},
            {"op": "write", "path": "textkit/slug.py", "content": SLUG},
            {
                "op": "write",
                "path": "tests/test_slug.py",
                "content": "from textkit.slug import slugify\n\n\ndef test_basic():\n"
                "    assert slugify('Hello, World!') == 'hello-world'\n",
            },
            {"op": "bash", "command": "{python} -m pytest -q -p no:cacheprovider"},
            {"op": "cost", "usd": 0.21},
            {"op": "finish", "message": "Added slugify with a test."},
        ],
        "count": [
            {"op": "text", "text": "Reading the package layout before adding word_count()."},
            {"op": "read", "path": "README.md"},
            {"op": "wait", "seconds": 0.6},
            {"op": "write", "path": "textkit/count.py", "content": COUNT},
            {"op": "write", "path": "tests/test_count.py", "content": COUNT_TESTS},
            {
                "op": "edit",
                "path": "README.md",
                "old": "Small, dependency-free text helpers.\n",
                "new": "Small, dependency-free text helpers.\n\n"
                "- `word_count(text)`: Unicode-aware word count.\n",
            },
            {"op": "bash", "command": "{python} -m pytest -q -p no:cacheprovider"},
            {"op": "bash", "command": "git push origin HEAD"},
            {
                "op": "text",
                "text": "Pushing is not allowed here, which is expected. The tests pass; "
                "the change is ready for review.",
            },
            {"op": "cost", "usd": 0.38},
            {"op": "finish", "message": "word_count() with tests and a README entry."},
        ],
        "whitespace": [
            {"op": "text", "text": "Adding normalise_space()."},
            {
                "op": "write",
                "path": "textkit/space.py",
                "content": "def normalise_space(text: str) -> str:\n"
                "    return ' '.join(text.split(' '))\n",
            },
            {"op": "cost", "usd": 0.12},
            {"op": "finish", "message": "Added normalise_space()."},
        ],
        "durations": [
            {"op": "text", "text": "Adding parse_duration() for ISO 8601 durations."},
            {
                "op": "write",
                "path": "textkit/duration.py",
                "content": "def parse_duration(text: str) -> int:\n"
                "    return int(text.strip('PTS'))\n",
            },
            {
                "op": "write",
                "path": "tests/test_duration.py",
                "content": "from textkit.duration import parse_duration\n\n\n"
                "def test_minutes():\n    assert parse_duration('PT2M') == 120\n",
            },
            {"op": "cost", "usd": 0.17},
            {"op": "finish", "message": "Added parse_duration()."},
        ],
        "titlecase": [
            {"op": "text", "text": "Looking at how titles are built today."},
            {"op": "read", "path": "README.md"},
            {"op": "cost", "usd": 0.05},
            {
                "op": "block",
                "reason": "Which locales must title case support? Turkish and Dutch need "
                "special rules (dotted I, the IJ digraph). Should I add a dependency for "
                "them, or support English only for now?",
            },
        ],
        "cancelled": [
            {"op": "text", "text": "Exploring a native extension."},
            {"op": "wait", "seconds": 30},
            {"op": "finish"},
        ],
    }


LIVE_SCRIPT: list[dict[str, Any]] = [
    {"op": "text", "text": "I'll add limit and cursor parameters to list_items()."},
    {"op": "read", "path": "inventory/handlers.py"},
    {"op": "bash", "command": "git log --oneline -5"},
    {"op": "wait", "seconds": 1.5},
    {
        "op": "write",
        "path": "inventory/handlers.py",
        "content": "ITEMS = [{'sku': f'SKU-{n:04d}', 'qty': n % 7} for n in range(250)]\n\n\n"
        "def list_items(limit: int = 50, cursor: int = 0):\n"
        "    limit = max(1, min(limit, 200))\n"
        "    page = ITEMS[cursor : cursor + limit]\n"
        "    nxt = cursor + limit if cursor + limit < len(ITEMS) else None\n"
        "    return {'items': page, 'next_cursor': nxt}\n",
    },
    {"op": "cost", "usd": 0.09},
    {"op": "bash", "command": "git push origin HEAD"},
    {
        "op": "text",
        "text": "The push was refused by policy, as it should be. Next: tests for the "
        "first page, the last page and an out-of-range cursor.",
    },
    {"op": "write", "path": ".env", "content": "DEBUG=1\n"},
    {"op": "wait", "seconds": 1800},
    {"op": "finish", "message": "Pagination added."},
]


class DemoReviewer:
    def __init__(self, verdicts: dict[int, ReviewResult]) -> None:
        self.verdicts = verdicts

    async def review(self, request: ReviewRequest) -> ReviewResult:
        return self.verdicts.get(
            request.card_id, ReviewResult(Verdict.MET, "The change does what the card asks.")
        )


def demo_settings(home: Path, port: int) -> Settings:
    return load_settings(home, web_port=port, max_concurrent_per_project=1, heartbeat_seconds=1)


async def seed(settings: Settings, db: Database, root: Path) -> dict[str, int]:
    textkit = make_repo(root, "textkit", TEXTKIT_FILES, TEXTKIT_TOML)
    inventory = make_repo(root, "inventory-api", INVENTORY_FILES, INVENTORY_TOML)
    ids: dict[str, int] = {}
    with db.session() as session:
        tk = store.add_project(
            session,
            slug="textkit",
            checkout_path=textkit,
            agent_path=root / "textkit-agent",
            spec=load_spec(textkit),
        )
        inv = store.add_project(
            session,
            slug="inventory-api",
            checkout_path=inventory,
            agent_path=root / "inventory-api-agent",
            spec=load_spec(inventory),
        )

        def card(project: Any, key: str, title: str, **kw: Any) -> None:
            ids[key] = store.add_card(session, project, title=title, **kw).id

        card(
            tk,
            "slugify",
            "Add slugify(text) helper",
            goal="Add slugify() that turns any title into a URL-safe slug.",
            acceptance_criteria="- slugify('Hello, World!') == 'hello-world'\n"
            "- accented letters become ASCII\n- covered by tests",
            complexity=Complexity.LOW,
            ready=True,
        )
        card(
            tk,
            "count",
            "Add word_count() with Unicode-aware splitting",
            goal="Count words in a string. Hyphenated words and contractions count as one "
            "word, and non-ASCII letters are part of words.",
            acceptance_criteria="- word_count('one two  three') == 3\n"
            "- 'well-known' and \"don't\" each count as one word\n"
            "- accented letters are part of words\n- the README lists the new function",
            ready=True,
            budget_usd=2.0,
        )
        card(
            tk,
            "whitespace",
            "Normalise whitespace in titles",
            goal="Collapse runs of any whitespace (tabs, newlines, non-breaking spaces) "
            "into a single space and trim the ends.",
            acceptance_criteria="- tabs and newlines collapse to one space\n"
            "- leading and trailing whitespace is removed\n- covered by tests",
            ready=True,
        )
        card(
            tk,
            "durations",
            "Parse ISO 8601 durations",
            goal="parse_duration('PT2M') returns seconds.",
            acceptance_criteria="- PT2M is 120\n- P1D is 86400\n- invalid input raises ValueError",
            ready=True,
        )
        card(
            tk,
            "titlecase",
            "Support locale-aware title case",
            goal="Title-case strings correctly for the user's locale.",
            complexity=Complexity.HIGH,
            ready=True,
        )
        card(
            tk,
            "cancelled",
            "Experiment: native extension for slugify",
            goal="See whether a compiled extension is worth it.",
            ready=True,
        )
        card(
            tk,
            "draft",
            "Document the public API in the README",
            goal="One short example per public function.",
        )
        card(
            tk,
            "ready",
            "Add truncate(text, width) with an ellipsis",
            goal="Shorten text to a width without cutting words in half.",
            acceptance_criteria="- never longer than width\n- ends with an ellipsis when cut",
            ready=True,
        )
        card(
            inv,
            "live",
            "Add pagination to GET /items",
            goal="Large stores time out listing every item. Add limit and cursor "
            "parameters with a next_cursor in the response.",
            acceptance_criteria="- default page size 50, maximum 200\n"
            "- next_cursor is null on the last page\n- tests for first, last and empty pages",
            ready=True,
            budget_usd=3.0,
        )
        card(
            inv,
            "inv-draft",
            "Return 404 for unknown SKUs",
            goal="GET /items/<sku> returns 404 with a JSON error body.",
        )

    by_id = {v: k for k, v in ids.items()}
    plans = scripts()

    def script_for(task: AgentTask) -> FakeScript:
        key = by_id.get(int(task.metadata.get("card_id", "0")), "")
        return FakeScript.parse(plans.get(key, [{"op": "finish"}]))

    verdicts = {
        ids["count"]: ReviewResult(
            Verdict.MET,
            "word_count() matches every criterion. The regular expression keeps hyphenated "
            "words and contractions together and uses Unicode word characters, and the tests "
            "exercise each case. The README lists the function.",
            confidence=0.86,
            findings=[
                CriterionFinding(
                    "word_count('one two  three') == 3", "met", "tests/test_count.py::test_ascii"
                ),
                CriterionFinding(
                    "'well-known' and \"don't\" each count as one word",
                    "met",
                    "Pattern \\w+(?:[-']\\w+)* joins them; covered by "
                    "test_unicode_and_joined_words",
                ),
                CriterionFinding(
                    "accented letters are part of words",
                    "met",
                    "re.UNICODE with \\w; café and naïve counted once each",
                ),
                CriterionFinding(
                    "the README lists the new function", "met", "README.md gains one bullet"
                ),
            ],
            fix_list=[],
            concerns=[
                "Numbers such as 3.14 count as two words; the card does not say what it expects."
            ],
        ),
        ids["whitespace"]: ReviewResult(
            Verdict.NOT_MET,
            "Only plain spaces are handled. Tabs, newlines and non-breaking spaces pass through "
            "unchanged, and there are no tests.",
            confidence=0.92,
            findings=[
                CriterionFinding(
                    "tabs and newlines collapse to one space",
                    "not_met",
                    "split(' ') splits on the space character only",
                ),
                CriterionFinding(
                    "leading and trailing whitespace is removed",
                    "partial",
                    "Ends are kept when they are tabs or newlines",
                ),
                CriterionFinding("covered by tests", "not_met", "No test file was added"),
            ],
            fix_list=[
                "Use str.split() with no argument, or a regular expression on \\s+, so "
                "every kind of whitespace collapses",
                "Add tests for tabs, newlines, non-breaking spaces and trimming",
            ],
        ),
    }
    engine = Engine(
        settings,
        db,
        FakeRuntime(script_for, state_dir=settings.home / "fake-sessions"),
        reviewer=DemoReviewer(verdicts),
    )
    for key in ("slugify", "count", "whitespace", "durations", "titlecase"):
        await engine.run_card(ids[key])
    await engine.approve(ids["slugify"])
    task = engine.submit(ids["cancelled"])
    await asyncio.sleep(1.0)
    await engine.cancel(ids["cancelled"])
    await task
    with db.session() as session:
        store.add_comment(
            session,
            store.get_card(session, ids["whitespace"]),
            "Also keep a single space between sentences; do not add two.",
            author=CommentAuthor.HUMAN,
            kind="comment",
        )
        store.add_comment(
            session,
            store.get_card(session, ids["count"]),
            "Looks good to me. Merging after one more read.",
            author=CommentAuthor.HUMAN,
            kind="comment",
        )
    await learn(settings, db, ids)
    return ids


LESSONS: dict[str, list[dict[str, Any]]] = {
    "count": [
        {
            "op": "add",
            "kind": "convention",
            "summary": "Each helper gets its own module, a test file and a README bullet",
            "body": "word_count() landed as textkit/count.py with tests/test_count.py and one line "
            "in the README list of functions.",
            "paths": ["README.md", "textkit/count.py", "tests/test_count.py"],
        }
    ],
    "whitespace": [
        {
            "op": "add",
            "kind": "pitfall",
            "summary": r"split(' ') only splits on spaces; use split() or \s+ for all whitespace",
            "body": "normalise_space() was sent back because tabs, newlines and non-breaking "
            "spaces passed through unchanged. Test each kind of whitespace.",
            "paths": ["textkit/space.py"],
        }
    ],
    "durations": [
        {
            "op": "add",
            "kind": "pitfall",
            "summary": "Durations are ISO 8601: PT2M is 120 seconds, not 2",
            "body": "Stripping the letters from the string loses the unit. Parse hours, minutes "
            "and seconds separately.",
            "paths": ["textkit/duration.py", "tests/test_duration.py"],
        }
    ],
    "titlecase": [
        {
            "op": "add",
            "kind": "decision",
            "summary": "Locale rules for title case are undecided; ask before adding a dependency",
            "body": "Turkish and Dutch need special rules. The card was blocked on this question.",
            "paths": [],
        }
    ],
}


async def learn(settings: Settings, db: Database, ids: dict[str, int]) -> None:
    """Three weeks of a person's working hours, an entry a person wrote, and one dream in
    which the librarian (scripted here) learns from the finished cards."""
    now = datetime.now().astimezone()
    for day in range(21, 0, -1):
        moment = now - timedelta(days=day)
        if moment.weekday() < 5:
            hours = list(range(8, 18)) + ([20, 21] if day % 3 == 0 else [])
        else:
            hours = [10, 11] if day % 2 else []
        for hour in hours:
            rhythm.record_signal(db, moment.replace(hour=hour, minute=15))
    kb = KnowledgeBase(settings.home, "textkit")
    kb.ensure()
    kb.save(
        Entry(
            id=new_id(),
            kind="command",
            summary="Run the tests with python -m pytest -q -p no:cacheprovider",
            body="The package has no dependencies; tests run straight from the checkout.",
            author="person",
            confidence="confirmed",
        )
    )
    kb.commit("Added by a person: how to run the tests")
    by_id = {v: k for k, v in ids.items()}

    def librarian(task: AgentTask) -> FakeScript:
        key = by_id.get(int(task.metadata.get("card_id", "0")), "")
        answer = json.dumps({"deltas": LESSONS.get(key, [])})
        return FakeScript.parse([{"op": "cost", "usd": 0.004}, {"op": "finish", "message": answer}])

    runtime = FakeRuntime(librarian, state_dir=settings.home / "fake-sessions")
    await run_dream(db, settings, runtime, trigger="manual", runtime_name="fake", forced=True)


async def serve(settings: Settings, db: Database, ids: dict[str, int]) -> None:
    import uvicorn

    def script_for(task: AgentTask) -> FakeScript:
        if int(task.metadata.get("card_id", "0")) == ids["live"]:
            return FakeScript.parse(LIVE_SCRIPT)
        return FakeScript.parse(
            [
                {"op": "text", "text": "Working on it."},
                {"op": "wait", "seconds": 2},
                {"op": "write", "path": "NOTES.md", "content": "demo\n"},
                {"op": "finish"},
            ]
        )

    engine = Engine(
        settings,
        db,
        FakeRuntime(script_for, state_dir=settings.home / "fake-sessions"),
        reviewer=DemoReviewer({}),
    )
    app = create_app(settings, db, engine, runtime_name="fake (demo)")
    config = uvicorn.Config(
        app, host="127.0.0.1", port=settings.web_port, log_level="warning", lifespan="on"
    )
    server = uvicorn.Server(config)

    async def start_live() -> None:
        while not server.started:  # noqa: ASYNC110 - uvicorn exposes a flag, not an event
            await asyncio.sleep(0.05)
        engine.submit(ids["live"])

    starter = asyncio.create_task(start_live())
    await server.serve()
    starter.cancel()


def restart_live(settings: Settings, db: Database, card_id: int) -> None:
    engine = Engine(settings.model_copy(update={"stale_run_seconds": 0}), db, FakeRuntime([]))
    engine.recover()
    asyncio.run(engine.cancel(card_id))
    with db.session() as session:
        card = store.get_card(session, card_id)
        if card.status == CardStatus.CANCELLED:
            store.set_card_status(session, card, CardStatus.DRAFT, "demo restart")
            store.set_card_status(session, card, CardStatus.READY, "demo restart")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--port", type=int, default=18300)
    args = parser.parse_args()
    root: Path = args.directory.resolve()
    # Gates say "python"; make that the interpreter running this script.
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    ids_file = root / "demo-ids.json"
    settings = demo_settings(root / "home", args.port)
    if ids_file.exists():
        # Serve an earlier demo again; restart the live card.
        db = Database(settings.db_url)
        ids = {k: int(v) for k, v in json.loads(ids_file.read_text(encoding="utf-8")).items()}
        restart_live(settings, db, ids["live"])
    else:
        if root.exists() and any(root.iterdir()):
            parser.error(f"{root} is not empty")
        root.mkdir(parents=True, exist_ok=True)
        settings.ensure_dirs()
        db = Database(settings.db_url)
        upgrade(db)
        ensure_access_token(settings)
        ids = asyncio.run(seed(settings, db, root))
        ids_file.write_text(json.dumps(ids), encoding="utf-8")
    print("seeded cards:", ", ".join(f"{k}={v}" for k, v in ids.items()), flush=True)
    print("open:", login_url(settings), flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(settings, db, ids))


if __name__ == "__main__":
    main()
