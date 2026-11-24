"""Build one feature with a real model and judge it with the case's hidden check.

    python evals/feature-bench/run.py <case dir> plain|single|swarm <work dir>

The runner behind PROTOCOL.md in this folder; bench.sh calls it once per run.

plain:  one plain Claude Code session with the card's text. No policy, gates or reviewer.
single: the whole feature as one card, one builder (reworked on failed gates, up to 3 runs).
        With PROOF_PREP=1 PRAT researches and checks a fact pack for the card first; that
        time is recorded as prep_seconds and is not part of seconds.
swarm:  the feature as an epic; the planner splits it, the split is approved at once, a swarm
        builds the parts on the epic's branch, and the epic comes to review once.
Prints a progress line every 60 s and writes result.json in the work dir.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

from sqlalchemy import select

from prat import store
from prat.config import load_settings
from prat.db import Card, Database, Run
from prat.db.migrate import upgrade
from prat.domain import CardKind, CardStatus
from prat.engine import Engine
from prat.engine.critic import AgentReviewer
from prat.engine.planner import AgentPlanner
from prat.engine.security import AgentSecurityReviewer
from prat.evals.runner import scratch_settings
from prat.git import Git
from prat.projects import load_spec
from prat.runtime.claude import ClaudeRuntime


def make_repo(case: Path, checkout: Path, source: dict | None, home: Path) -> None:
    if source:
        # An open-source starting point: the files of that commit, then the case's overlay.
        from prat.evals.cases import Source
        from prat.evals.runner import source_mirror

        mirror = source_mirror(home, Source(repository=source["repository"], commit=source["commit"]))
        checkout.mkdir(parents=True)
        archive = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "--git-dir", str(mirror), "archive", source["commit"]],
            capture_output=True, check=True,
        ).stdout
        import io
        import tarfile

        # Not the tar on PATH: under Git Bash it reads "C:\..." as a remote host.
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(checkout, filter="fully_trusted")
    shutil.copytree(
        case / "repo", checkout, ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True
    )
    git = Git(checkout)
    git.run("init", "--quiet", "--initial-branch", "main")
    git.config_set("user.name", "Proof")
    git.config_set("user.email", "proof@prat.invalid")
    git.config_set("core.autocrlf", "false")
    git.run("add", "--all")
    git.run("commit", "--quiet", "--no-verify", "-m", "Starting point")


def hidden_check(case: Path, agent: Path, commit: str, where: Path) -> dict:
    if where.exists():
        shutil.rmtree(where, ignore_errors=True)
    Git(agent).run("worktree", "add", "--quiet", "--detach", str(where), commit)
    shutil.copyfile(case / "hidden" / "hidden_check.py", where / "hidden_check.py")
    out = subprocess.run(
        [sys.executable, "hidden_check.py"], cwd=where, capture_output=True, text=True, timeout=900
    )
    tail = (out.stdout + out.stderr).strip().splitlines()[-6:]
    ran = next((line for line in tail if line.startswith("Ran ")), "")
    return {"passed": out.returncode == 0, "ran": ran, "tail": tail}


def costs(db: Database) -> tuple[float, list[dict]]:
    total, runs = 0.0, []
    with db.session() as session:
        for run in session.scalars(select(Run).order_by(Run.id)):
            total += run.cost_usd or 0.0
            critique = next((s for s in run.steps if str(s.name) == "critique"), None)
            total += float(((critique.detail or {}) if critique else {}).get("cost_usd") or 0.0)
            runs.append(
                {
                    "run": run.id,
                    "card": run.card_id,
                    "title": run.card.title,
                    "status": str(run.status),
                    "started": run.started_at.isoformat() if run.started_at else None,
                    "finished": run.finished_at.isoformat() if run.finished_at else None,
                    "cost_usd": round(run.cost_usd or 0.0, 4),
                }
            )
    return round(total, 4), runs


def statuses(db: Database) -> str:
    with db.session() as session:
        cards = list(session.scalars(select(Card).order_by(Card.id)))
        return ", ".join(
            f"#{c.id} {c.status}" + (f" (held: {c.hold_reason})" if c.hold_reason else "")
            for c in cards
        )


async def plain(case: Path, spec_text: dict, scratch, checkout: Path, work: Path, started: float) -> None:
    """The baseline: one plain Claude Code session builds the whole feature."""
    from prat.evals.plain import run_plain

    spec = load_spec(checkout, default_name="proof")
    parts = (
        f"# {spec_text['title']}", spec_text["goal"].strip(),
        "## Acceptance criteria", spec_text["acceptance_criteria"].strip(),
        "## About this project", spec.notes.strip(),
    )
    prompt = "\n\n".join(part for part in parts if part) + "\n"
    import os

    # PROOF_PLAIN_EFFORT=low: the baseline at the effort PRAT's builder uses by default.
    effort = os.environ.get("PROOF_PLAIN_EFFORT") or None
    session = await run_plain(
        scratch, None, spec, checkout, journal=work / "plain.jsonl", prompt=prompt, effort=effort
    )
    elapsed = time.monotonic() - started
    git = Git(checkout)
    git.run("add", "--all")
    git.run("commit", "--quiet", "--no-verify", "--allow-empty", "-m", "Claude Code's work")
    commit = git.head()
    check = hidden_check(case, checkout, commit, work / "check")
    result = {
        "mode": "plain", "effort": effort or "default",
        "final_status": session.status, "reason": session.reason,
        "seconds": round(elapsed, 1), "cost_usd": round(session.cost_usd, 4),
        "turns": session.turns, "hidden_check": check, "commit": commit,
    }
    (work / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)


async def main() -> None:
    case, mode, work = Path(sys.argv[1]).resolve(), sys.argv[2], Path(sys.argv[3]).resolve()
    spec_text = tomllib.loads((case / "case.toml").read_text(encoding="utf-8"))
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    base = load_settings()
    if base.auth_mode != "claude_login":
        raise SystemExit(f"auth_mode is {base.auth_mode}; this proof runs only on a Claude login")
    scratch = scratch_settings(base, work / "home").model_copy(
        update={
            "swarm_epics": mode == "swarm",
            "max_concurrent_runs": 12,
            "max_concurrent_per_project": 12,
            "governor_poll_seconds": 2.0,
            "memory_reserve_mb": 512,
        }
    )
    # PROOF_SET="race_builders=1,epic_parts_by_reference=false" overrides settings for one run.
    import os
    if os.environ.get("PROOF_PACK"):
        head = (
            "\n\n## Verified facts about this code base\n\n"
            "Each was checked against the code; line numbers are for the commit you start from.\n\n"
        )
        spec_text["goal"] = (
            spec_text["goal"].rstrip() + head + Path(os.environ["PROOF_PACK"]).read_text(encoding="utf-8")
        )
        print("pack:", os.environ["PROOF_PACK"], flush=True)
    extra = {}
    for pair in filter(None, os.environ.get("PROOF_SET", "").split(",")):
        key, _, value = pair.partition("=")
        extra[key.strip()] = json.loads(value) if value.strip() in ("true", "false") or value.strip().isdigit() else value.strip()
    # PROOF_MODELS="builder=claude-haiku-5-5;critic=..." and PROOF_EFFORTS="builder=low;critic=low".
    for name, env in (("models", "PROOF_MODELS"), ("efforts", "PROOF_EFFORTS")):
        table = dict(p.split("=", 1) for p in filter(None, os.environ.get(env, "").split(";")))
        if table:
            extra[name] = table
    if extra:
        scratch = scratch.model_copy(update=extra)
        print(f"settings: {extra}", flush=True)
    scratch.ensure_dirs()
    checkout, agent = work / "repo", work / "agent"
    make_repo(case, checkout, spec_text.get("source"), base.home)
    if os.environ.get("PROOF_PACK_NOTES"):
        # The pack in the project's notes: the builder reads them, the reviewer does not.
        toml = checkout / ".prat" / "project.toml"
        notes = tomllib.loads(toml.read_text(encoding="utf-8")).get("notes", "")
        pack = Path(os.environ["PROOF_PACK_NOTES"]).read_text(encoding="utf-8")
        # Notes are capped at 4,000 characters, so the pack is a file the notes point at.
        (checkout / "FACT_PACK.md").write_text(
            "# Facts about this code base for the card in hand\n\n"
            "Prepared by a research pass over the code; line numbers are for the commit you "
            "start from. They are help, not requirements: the card says what to build.\n\n" + pack,
            encoding="utf-8",
        )
        whole = (
            notes + " Before anything else, read FACT_PACK.md in the repository root in full: "
            "a research pass has already found the files, signatures, call sites, the feature "
            "to imitate and the traps for the card in hand, so you should not need to explore."
        )
        assert '"' not in whole and len(whole) < 4000
        lines = toml.read_text(encoding="utf-8").splitlines()
        lines = [f'notes = "{whole}"' if line.startswith("notes = ") else line for line in lines]
        toml.write_text("\n".join(lines) + "\n", encoding="utf-8")
        Git(checkout).run("add", "--all")
        Git(checkout).run("commit", "--quiet", "--no-verify", "-m", "Project notes")
        print("pack in notes:", os.environ["PROOF_PACK_NOTES"], flush=True)
    if mode == "plain":
        await plain(case, spec_text, scratch, checkout, work, started := time.monotonic())
        return
    db = Database(scratch.db_url)
    upgrade(db)
    with db.session() as session:
        project = store.add_project(
            session,
            slug="tinyshop",
            checkout_path=checkout,
            agent_path=agent,
            spec=load_spec(checkout, default_name="tinyshop"),
        )
        card = store.add_card(
            session,
            project,
            title=spec_text["title"],
            goal=spec_text["goal"],
            acceptance_criteria=spec_text["acceptance_criteria"],
            complexity=spec_text.get("complexity", "high"),
            kind=CardKind.EPIC if mode == "swarm" else CardKind.CHANGE,
            ready=True,
        )
        card_id = card.id
    real = ClaudeRuntime.from_settings(scratch)
    engine = Engine(
        scratch,
        db,
        real,
        reviewer=AgentReviewer(real),
        planner=AgentPlanner(real),
        security_reviewer=AgentSecurityReviewer(real),
    )
    prepared: dict = {}
    if os.environ.get("PROOF_PREP"):
        # Backlog time: the card is researched and checked before anyone asks for it.
        prep = time.monotonic()
        pack = await engine.prepare_pack(card_id)
        prepared = {
            "prep_seconds": round(time.monotonic() - prep, 1),
            "prep_cost_usd": round(pack.cost_usd, 4),
            "pack_status": str(pack.status),
            "pack_lines": len(pack.text.splitlines()),
        }
        print(f"prep: {time.monotonic() - prep:.0f}s research {pack.research_seconds:.0f}s check {pack.verify_seconds:.0f}s status {pack.status} corrections {pack.corrections} cost ${pack.cost_usd:.2f} lines {len(pack.text.splitlines())}", flush=True)
    started = time.monotonic()
    commit: str | None = None
    final = ""

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(60)
            mins = (time.monotonic() - started) / 60
            print(f"[{mins:5.1f} min] {statuses(db)}", flush=True)

    beat = asyncio.create_task(heartbeat())
    try:
        if mode == "single":
            for attempt in range(1, 4):
                outcome = await engine.run_card(card_id)
                final = str(outcome.card_status)
                print(f"run {attempt}: {final} {outcome.reason or ''}", flush=True)
                if outcome.card_status == CardStatus.REVIEW:
                    break
                if outcome.card_status == CardStatus.FAILED:
                    engine.rework(card_id, "The gates failed; read their output and fix it.")
                elif outcome.card_status != CardStatus.REWORK:
                    break
            with db.session() as session:
                branch = store.get_card(session, card_id).branch
        else:
            outcome = await engine.run_card(card_id)
            print(f"split: {outcome.card_status} {outcome.reason or ''}", flush=True)
            if outcome.card_status != CardStatus.REVIEW:
                raise SystemExit("the planner did not produce a split")
            with db.session() as session:
                card = store.get_card(session, card_id)
                one_card = card.kind is CardKind.CHANGE
                final, branch = str(card.status), card.branch
            if one_card:
                print("the planner built it as one card", flush=True)
            else:
                split_at = time.monotonic() - started
                await engine.approve(card_id)
                print(f"split approved after {split_at:.0f}s: {statuses(db)}", flush=True)
            while not one_card:
                with db.session() as session:
                    epic = store.get_card(session, card_id)
                    if epic.status != CardStatus.BUILDING:
                        final, branch = str(epic.status), epic.branch
                        break
                if time.monotonic() - started > 3 * 3600:
                    raise SystemExit("gave up after 3 hours")
                await asyncio.sleep(5)
        elapsed = time.monotonic() - started
        if branch and Git(agent).ref_exists(f"refs/heads/{branch}"):
            commit = Git(agent).rev_parse(f"refs/heads/{branch}")
        check = hidden_check(case, agent, commit, work / "check") if commit else None
        total, runs = costs(db)
        result = {
            "mode": mode,
            "final_status": final,
            "seconds": round(elapsed, 1),
            "cost_usd": total,
            "runs": runs,
            "hidden_check": check,
            "commit": commit,
            "statuses": statuses(db),
            **prepared,
        }
        (work / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in result.items() if k != "runs"}, indent=2), flush=True)
    finally:
        beat.cancel()
        db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
