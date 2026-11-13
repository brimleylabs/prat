"""Claude Code's own memory for the benchmark (see ../PROTOCOL.md).

    python claude_memory.py init SNAPSHOT OUT_DIR
    python claude_memory.py learn SNAPSHOT LEARNING_CASES CLAUDE_MD OUT_DIR [--person FILE]

``init`` asks plain Claude Code, in a copy of the snapshot repository, to run /init and keeps
the CLAUDE.md it writes. ``learn`` works through the learning-batch cards one after another in
one copy of the snapshot that starts with CLAUDE_MD; after each card Claude Code is told to add
to CLAUDE.md what a future developer should know. Both write the final CLAUDE.md, a journal of
every session, and notes.json (what ran, and how) to OUT_DIR.

With ``--person FILE`` (ADDENDUM-3.md), a person checks each card's result the way they do on
PRAT's side (the case's hidden check) and, if it fails, sends the comment in FILE back, word for
word, in a new session; up to 3 sessions per card, the same as PRAT's runs.

Runs with PRAT's settings (PRAT_HOME, its auth mode and builder model) so both sides use the
same model and budget.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from prat.config import load_settings
from prat.evals import load_cases
from prat.evals.cases import EvalCase
from prat.evals.plain import plain_prompt, run_plain
from prat.evals.runner import hidden_check
from prat.projects import load_spec

INIT_FALLBACK = (
    "Please analyze this codebase and create a CLAUDE.md file, which will be given to future "
    "instances of Claude Code to operate in this repository. Add: the commands that will be "
    "commonly used, such as how to build, lint and run tests, including how to run a single "
    "test; and the high-level code architecture and structure, so future instances can be "
    "productive more quickly. Focus on the big picture that takes reading several files to "
    "understand."
)
SENT_BACK = (
    "A reviewer sent your change for this card back with this comment:\n\n{comment}\n\n"
    "Address it. The earlier work is already committed."
)
MAX_SESSIONS = 3
LEARN_SUFFIX = (
    "\n\nWhen you are done, add to CLAUDE.md anything a future developer working on this "
    "codebase should know, from what you learned here."
)


def _writable(func, path, _exc) -> None:
    os.chmod(path, stat.S_IWRITE)  # git marks its objects read-only, which stops Windows deleting them
    func(path)


def copy_repo(snapshot: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest, onexc=_writable)
    subprocess.run(["git", "clone", "-q", str(snapshot), str(dest)], check=True)
    for key, value in (("user.name", "Claude Code"), ("user.email", "claude@example.invalid")):
        subprocess.run(["git", "config", key, value], cwd=dest, check=True)
    return dest


def commit(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "--no-verify", "--allow-empty", "-m", message], cwd=repo, check=True)


async def session(settings, case: EvalCase, repo: Path, prompt: str, journal: Path) -> dict:
    spec = load_spec(repo, default_name="rich")
    result = await run_plain(settings, case, spec, repo, journal=journal, prompt=prompt)
    return {"status": result.status, "reason": result.reason, "turns": result.turns}


async def init(snapshot: Path, out: Path) -> None:
    settings = load_settings()
    repo = copy_repo(snapshot, out / "repo")
    case = load_cases(Path(__file__).resolve().parents[1] / "cases" / "learning")[0]  # only for its type
    ran = await session(settings, case, repo, "/init", out / "init.jsonl")
    used = "/init"
    if not (repo / "CLAUDE.md").is_file():
        ran = await session(settings, case, repo, INIT_FALLBACK, out / "init-fallback.jsonl")
        used = "the /init prompt, sent as text (the /init command did not write CLAUDE.md)"
    if not (repo / "CLAUDE.md").is_file():
        sys.exit("no CLAUDE.md was written")
    shutil.copyfile(repo / "CLAUDE.md", out / "CLAUDE.md")
    (out / "notes.json").write_text(json.dumps({"init": used, "session": ran}, indent=2))


async def learn(
    snapshot: Path, cases_dir: Path, claude_md: Path, out: Path, person: str | None = None
) -> None:
    settings = load_settings()
    repo = copy_repo(snapshot, out / "repo")
    shutil.copyfile(claude_md, repo / "CLAUDE.md")
    commit(repo, "CLAUDE.md")
    notes = []
    for case in load_cases(cases_dir):
        spec = load_spec(repo, default_name="rich")
        prompt = plain_prompt(case, spec) + LEARN_SUFFIX
        sessions = []
        for number in range(1, MAX_SESSIONS + 1):
            journal = out / f"learn-{case.name}-{number}.jsonl"
            ran = await session(settings, case, repo, prompt, journal)
            commit(repo, f"Claude Code: {case.spec.title}")
            sessions.append(ran)
            if person is None:
                break
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
            ).stdout.strip()
            check = hidden_check(case, settings, repo, head, out / f"check-{case.name}-{number}", False)
            ran["person_check"] = check.passed
            if check.passed:
                break
            ran["sent_back"] = person
            prompt = SENT_BACK.format(comment=person) + LEARN_SUFFIX
        notes.append({"case": case.name, "sessions": sessions})
    shutil.copyfile(repo / "CLAUDE.md", out / "CLAUDE.md")
    (out / "notes.json").write_text(json.dumps(notes, indent=2))


def main() -> None:
    command, *args = sys.argv[1:]
    person = None
    if "--person" in args:
        at = args.index("--person")
        person = Path(args[at + 1]).read_text(encoding="utf-8").strip()
        del args[at : at + 2]
    paths = [Path(a).resolve() for a in args]
    paths[-1].mkdir(parents=True, exist_ok=True)
    if command == "init":
        asyncio.run(init(*paths))
    elif command == "learn":
        asyncio.run(learn(*paths, person=person))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
