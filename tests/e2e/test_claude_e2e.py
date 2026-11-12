"""A real run with the Claude runtime. Skipped unless PRAT_E2E=1.

Uses Haiku for every role and a $1 budget. Authentication follows
``PRAT_E2E_AUTH`` (``api_key``, the default, or ``claude_login`` for your own
login on your own plan). Read at collection time, before the test fixtures
clear ``PRAT_*`` variables.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from conftest import make_repo
from prat import store
from prat.config import load_settings
from prat.db import Database
from prat.db.migrate import upgrade
from prat.domain import CardStatus, CommentAuthor
from prat.engine import Engine
from prat.engine.critic import AgentReviewer
from prat.engine.planner import AgentPlanner
from prat.journal import read_journal
from prat.model_registry import HAIKU
from prat.projects import load_spec
from prat.runtime.claude import ClaudeRuntime

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

AUTH_MODE = os.environ.get("PRAT_E2E_AUTH", "api_key")
PY = Path(sys.executable).as_posix()

TOML = f"""
name = "textkit"
default_branch = "main"
autonomy = "medium"
notes = "A tiny library. Code in textkit.py, tests in test_textkit.py (pytest)."

[commands]
test = ['{PY}', '-m', 'pytest', '-q']

[verify]
gates = ["test"]
timeout_seconds = 300
"""

FILES = {
    "textkit.py": 'def shout(text: str) -> str:\n    return text.upper() + "!"\n',
    "test_textkit.py": (
        "from textkit import shout\n\n\ndef test_shout() -> None:\n"
        '    assert shout("hi") == "HI!"\n'
    ),
}


async def test_real_card_reaches_review_with_a_verdict(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repos", "textkit", project_toml=TOML, files=FILES)
    settings = load_settings(
        tmp_path / "home",
        auth_mode=AUTH_MODE,
        models={"builder": HAIKU, "critic": HAIKU, "planner": HAIKU},
        default_budget_usd=1.0,
    )
    settings.ensure_dirs()
    db = Database(settings.db_url)
    upgrade(db)
    try:
        with db.session() as session:
            project = store.add_project(
                session,
                slug="textkit",
                checkout_path=repo,
                agent_path=repo.parent / "textkit-agent",
                spec=load_spec(repo),
            )
            card = store.add_card(
                session,
                project,
                title="Add slugify",
                goal="Add slugify(text) to textkit.py with tests.",
                acceptance_criteria=(
                    "- slugify lowercases, replaces runs of non-alphanumerics with one dash, "
                    "and strips leading and trailing dashes\n- tests in test_textkit.py cover it"
                ),
                ready=True,
            )
            card_id = card.id
        runtime = ClaudeRuntime.from_settings(settings)
        engine = Engine(
            settings,
            db,
            runtime,
            reviewer=AgentReviewer(runtime),
            planner=AgentPlanner(runtime),
        )
        outcome = await engine.run_card(card_id)
        assert outcome.card_status in (CardStatus.REVIEW, CardStatus.REWORK), outcome.reason
        with db.session() as session:
            card = store.get_card(session, card_id)
            verdicts = [c for c in card.comments if c.author == CommentAuthor.REVIEWER]
            assert verdicts
            run = card.runs[-1]
            assert run.cost_usd <= 1.0
            journal_path = Path(run.journal_path or "")
        critic_tools = [
            e.data["tools"] for e in read_journal(journal_path) if e.kind == "critic.session"
        ]
        assert critic_tools and not {"Write", "Edit", "Bash"} & set(critic_tools[0])
        if outcome.card_status is CardStatus.REVIEW:
            merged = await engine.approve(card_id)
            assert merged.commit
    finally:
        db.dispose()
