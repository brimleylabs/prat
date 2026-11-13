"""PRAT's learning batch (see ../PROTOCOL.md and ADDENDUM-1.md).

    python prat_learning.py PRAT_HOME PROJECT_SLUG CASES_DIR OUT_JSON [--no-dream] [--person FILE]

Runs each case in CASES_DIR as an ordinary card on the project, the way a person works: a run
the reviewer sends back runs again with its fix list; a run whose gates fail is sent back with
their output; a card that reaches review is approved (merged locally). Up to 3 runs per card.
Then one dream cycle. Writes what happened, and the knowledge entries the dream added or
changed, to OUT_JSON.

With ``--person FILE`` (ADDENDUM-3.md), a person looks at each card that reaches review before
approving it: they run the case's hidden check on the card branch, and if it fails they send the
card back with the comment in FILE, word for word.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from prat import store
from prat.cli import _make_engine, _make_runtime
from prat.config import load_settings
from prat.domain import CardStatus, Complexity
from prat.dolt.store import open_store
from prat.dream import run_dream
from prat.evals import load_cases
from prat.evals.runner import gate_feedback, hidden_check, read_attempt
from prat.overlay import OverlayGuidedRuntime

MAX_RUNS = 3


def knowledge_snapshot(home: Path, slug: str) -> dict[str, str]:
    entries = home / "knowledge" / "projects" / slug / "entries"
    return {f.name: f.read_text(encoding="utf-8") for f in sorted(entries.glob("*.md"))}


async def main() -> None:
    home, slug, cases_dir, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
    dream = "--no-dream" not in sys.argv
    person = None
    if "--person" in sys.argv:
        person = Path(sys.argv[sys.argv.index("--person") + 1]).read_text(encoding="utf-8").strip()
    settings = load_settings(home)
    handle = open_store(settings)
    db = handle.db
    report: dict = {"cards": [], "dream": None}
    try:
        engine = _make_engine(settings, db, _make_runtime(settings, "claude", None))
        for case in load_cases(cases_dir):
            with db.session() as session:
                project = store.get_project(session, slug)
                card_id = store.add_card(
                    session,
                    project,
                    title=case.spec.title,
                    goal=case.spec.goal,
                    acceptance_criteria=case.spec.acceptance_criteria,
                    complexity=Complexity(case.spec.complexity),
                    ready=True,
                ).id
            runs = []
            for number in range(1, MAX_RUNS + 1):
                outcome = await engine.run_card(card_id)
                attempt = read_attempt(db, settings, outcome.run_id, number)
                attempt.card_status = str(outcome.card_status)
                attempt.reason = outcome.reason
                runs.append(
                    {
                        "run": number,
                        "card_status": attempt.card_status,
                        "reason": attempt.reason,
                        "gates": {g.name: g.passed for g in attempt.gates},
                        "verdict": attempt.verdict,
                    }
                )
                if outcome.card_status == CardStatus.REVIEW and person is not None:
                    with db.session() as session:
                        card = store.get_card(session, card_id)
                        clone, branch = Path(card.project.agent_path), card.branch
                    commit = subprocess.run(
                        ["git", "-C", str(clone), "rev-parse", branch],
                        capture_output=True, text=True, check=True,
                    ).stdout.strip()  # fmt: skip
                    folder = settings.home / "person-checks" / f"card-{card_id}-run-{number}"
                    check = hidden_check(case, settings, clone, commit, folder, False)
                    runs[-1]["person_check"] = check.passed
                    if not check.passed:
                        if number < MAX_RUNS:
                            engine.rework(card_id, person)
                            runs[-1]["sent_back"] = person
                            continue
                        break
                if outcome.card_status == CardStatus.REVIEW:
                    merged = await engine.approve(card_id)
                    runs[-1]["merged"] = merged.commit[:12]
                    break
                if outcome.card_status == CardStatus.REWORK:
                    continue
                if outcome.card_status == CardStatus.FAILED and attempt.steps.get("verify") == "failed":
                    engine.rework(card_id, gate_feedback(attempt))
                    continue
                break
            report["cards"].append({"case": case.name, "card": card_id, "runs": runs})
            print(json.dumps(report["cards"][-1]), flush=True)
        if dream:
            before = knowledge_snapshot(settings.home, slug)
            agent = OverlayGuidedRuntime(_make_runtime(settings, "claude", None), settings.home)
            result = await run_dream(db, settings, agent, trigger="manual", runtime_name="claude", forced=True)
            after = knowledge_snapshot(settings.home, slug)
            report["dream"] = {
                "id": result.dream_id,
                "status": result.status,
                "remember": result.phases.get("remember"),
                "added": {k: v for k, v in after.items() if k not in before},
                "changed": {k: v for k, v in after.items() if k in before and before[k] != v},
                "removed": sorted(set(before) - set(after)),
            }
            print(f"dream {result.status}: {len(report['dream']['added'])} added, "
                  f"{len(report['dream']['changed'])} changed", flush=True)  # fmt: skip
    finally:
        handle.close()
    out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    subprocess.run(["git", "-C", str(settings.home / "knowledge" / "projects" / slug), "log", "--oneline", "-5"])


if __name__ == "__main__":
    asyncio.run(main())
