"""Knowledge for one agent on one card: what the engine calls before it starts a session.

The card's text seeds the code map of the commit the card starts from; the entries most relevant
to it go into the prompt within the role's budget. Knowledge is a help, never a requirement: any
problem reading it gives the agent nothing rather than failing the run.
"""

from __future__ import annotations

import logging
from pathlib import Path

from prat.git import Git
from prat.knowledge.codemap import CodeMap, load_or_build
from prat.knowledge.entries import KnowledgeBase
from prat.knowledge.select import Selection, ranks_for, select

log = logging.getLogger(__name__)

BUDGETS = {"builder": 6000, "planner": 5000, "critic": 3000, "security_reviewer": 2000}


def code_map(kb: KnowledgeBase, worktree: Path) -> CodeMap | None:
    head = Git(worktree).run("rev-parse", "HEAD", check=False)
    if not head.ok or not head.stdout.strip():
        return None
    return load_or_build(worktree, head.stdout.strip(), kb.cache_dir())


def for_card(home: Path, slug: str, worktree: Path, card_text: str, role: str) -> Selection:
    """The knowledge section for ``role`` on a card, or an empty selection."""
    try:
        kb = KnowledgeBase(home, slug)
        if not kb.entries_dir.is_dir():
            return Selection()
        entries = kb.entries()
        if not entries:
            return Selection()
        ranks = ranks_for(code_map(kb, worktree), card_text)
        return select(entries, role=role, ranks=ranks, budget=BUDGETS.get(role, 4000))
    except Exception:
        log.exception("could not select knowledge for %s (%s)", slug, role)
        return Selection()
