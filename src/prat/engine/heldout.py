"""Tests written from the card alone, and run on the work.

A builder writes the code and the tests that pass on it, so its tests share its reading of the
card. With ``held_out_tests`` on, a second session that never sees the builder's work writes
one test file from the card's own words while the builder works, reading the code as it was
before the card. When the builder hands its work over and the project's own checks pass, PRAT
puts that file in the worktree, runs it, and takes it out again.

A failure is not a verdict: the test can be the one that is wrong about the card. It goes to
the builder once, with the test, to fix the work or say why the test is mistaken, and the
reviewer is told how it ended.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from prat import paths
from prat.domain import Autonomy
from prat.engine import evidence
from prat.packs import card_hash, card_text
from prat.policy import Policy
from prat.runtime.base import READ_TOOLS, AgentRuntime, AgentTask, Budget, Outcome
from prat.runtime.session import collect

HELD_DIR = "heldout"
MAX_TEST_CHARS = 30_000
NOTE = "heldout.md"

SYSTEM = """\
You write one test file for the card below, from the card's own words. Another engineer is
implementing the card right now; you will not see that work and it does not exist in the code
in front of you. You change no files: use your read-only tools to learn how tests in this
project are written, where they live and how one file of them is run.

Rules:
- Test the behaviour the acceptance criteria state, through the names and interfaces the card
  gives. Where the card leaves a choice open (a private name, the wording of a message, an
  order it does not fix), do not pin it: a test that fails on a correct implementation is
  worse than no test.
- Write in this project's own test framework and style, as a new file where its tests live.
- The tests must fail on the code as it is now and pass once the card is done.
- Keep it short: the few cases the card is really about, edges it names included.

Your final answer is exactly one JSON object in a fenced json block:
{"path": "path/of/the/new/test/file from the repository root",
 "content": "the whole file",
 "command": "the command that runs only this file, from the repository root"}
If the card cannot be tested this way (it is about looks, or documentation), answer with empty
strings and a "why".
"""

_JSON = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.S)


@dataclass
class HeldOut:
    card_id: int
    card_hash: str
    path: str = ""
    content: str = ""
    command: str = ""
    why: str = ""
    cost_usd: float = 0.0
    seconds: float = 0.0

    @property
    def usable(self) -> bool:
        return bool(self.path.strip() and self.content.strip() and self.command.strip())


def path_for(home: Path, card_id: int) -> Path:
    return home / HELD_DIR / f"card-{card_id}.json"


def load(home: Path, card_id: int) -> HeldOut | None:
    try:
        return HeldOut(**json.loads(path_for(home, card_id).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def save(home: Path, held: HeldOut) -> None:
    path = path_for(home, held.card_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(held), indent=1), encoding="utf-8")


def parse(text: str) -> dict[str, str]:
    """The test a session answered with; empty strings when it gave none."""
    found = _JSON.findall(text or "")
    for raw in reversed(found or [text or ""]):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict):
            return {key: str(data.get(key) or "") for key in ("path", "content", "command", "why")}
    return {"path": "", "content": "", "command": "", "why": "no test in the answer"}


async def write(
    runtime: AgentRuntime,
    *,
    card_id: int,
    title: str,
    goal: str,
    acceptance_criteria: str,
    workdir: Path,
    model: str | None,
    budget_usd: float = 1.0,
    on_event: Callable[[Any], Any] | None = None,
    test_command: str = "",
) -> HeldOut:
    """Have a read-only session write the card's test from the code as it is in ``workdir``.
    ``test_command`` is the project's own, so the session's command runs where that one does."""
    held = HeldOut(card_id=card_id, card_hash=card_hash(title, goal, acceptance_criteria))
    began = time.monotonic()
    how = (
        f"\n\n## How this project's tests are run\n`{test_command}`\nYour command must work in "
        "the same environment: build it from this one (read what it runs), limited to your "
        "file, and leave out options that need plugins this one does not use."
        if test_command
        else ""
    )
    out = await collect(
        runtime,
        AgentTask(
            prompt=card_text(title, goal, acceptance_criteria) + how,
            system_prompt=SYSTEM,
            workdir=workdir,
            allowed_tools=READ_TOOLS,
            policy=Policy(Autonomy.READ_ONLY, workdir),
            budget=Budget(max_usd=budget_usd),
            model=model,
            metadata={"role": "researcher", "card_id": str(card_id)},
        ),
        on_event=on_event,
    )
    held.seconds = round(time.monotonic() - began, 1)
    held.cost_usd = out.cost_usd
    if out.outcome is not Outcome.COMPLETED:
        held.why = f"{out.outcome or 'no result'} {out.message or ''}".strip()
        return held
    answer = parse(out.final_text)
    held.path = answer["path"].strip().replace("\\", "/").lstrip("/")
    held.content = answer["content"][:MAX_TEST_CHARS]
    held.command = answer["command"].strip()
    held.why = answer["why"]
    return held


def run(
    held: HeldOut,
    worktree: Path,
    *,
    allowed: Callable[[str], str | None],
    env: Mapping[str, str],
    timeout: float,
) -> evidence.Ran:
    """Put the test in ``worktree``, run it, and take it out again. A test that would land
    outside the worktree is not run. When the builder wrote a file of the same name (both
    reached for the obvious one), the test goes in beside it under a name of its own."""
    target = paths.resolve(worktree / held.path)
    if not paths.is_within(target, paths.resolve(worktree)) or ".git" in Path(held.path).parts:
        return evidence.Ran(held.command, None, refused="the test's path is outside the work")
    command = held.command
    if target.exists():
        spare = target.with_name(f"{target.stem}_heldout{held.card_id}{target.suffix}")
        if spare.exists() or held.path not in command:
            return evidence.Ran(command, None, refused=f"{held.path} is already there")
        own = held.path[: len(held.path) - len(target.name)] + spare.name
        command, target = command.replace(held.path, own), spare
    made: list[Path] = []
    try:
        folder = target.parent
        while not folder.exists():
            made.append(folder)
            folder = folder.parent
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(held.content, encoding="utf-8")
        ran = evidence.run([command], worktree, allowed=allowed, env=env, timeout=timeout, limit=1)
        return ran[0] if ran else evidence.Ran(command, None, refused="no command")
    finally:
        target.unlink(missing_ok=True)
        for folder in made:
            try:
                folder.rmdir()
            except OSError:
                break


def feedback(held: HeldOut, ran: evidence.Ran) -> str:
    return "\n".join(
        [
            "Before you finish: a test written from the card alone, by a session that has not "
            "seen your work, fails on it. The test may be right, or it may be mistaken about "
            "what the card asks. If it is right, fix your work. If it is mistaken, change "
            "nothing for it and say in your final message which assertion is wrong and why, "
            "so a person decides. The test file is not part of your work: do not add it.",
            "",
            f"Ran: {ran.command} (exit {ran.exit_code})",
            ran.output[-1500:],
            "",
            f"The test ({held.path}):",
            held.content[:6000],
        ]
    )


def review_note(held: HeldOut, ran: evidence.Ran | None, after: evidence.Ran | None) -> str:
    """What the reviewer is told about the card's held-out test."""
    if ran is None:
        return ""
    lines = ["## A test written from the card alone"]
    last = after or ran
    if ran.refused or ran.timed_out or ran.misused:
        why = ran.refused or ("it did not finish" if ran.timed_out else f"exit {ran.exit_code}")
        return "\n".join([*lines, f"It could not be run ({why}), so it says nothing."])
    if last.exit_code == 0:
        first = "" if ran.exit_code == 0 else " after the builder was shown it failing"
        return "\n".join([*lines, f"It passes on this work{first}: `{held.command}`."])
    lines += [
        f"It still fails on this work (`{held.command}`, exit {last.exit_code}). It was written "
        "without sight of the work and may be mistaken about the card: read the builder's "
        "answer to it, then judge which is right.",
        "",
        last.output[-1200:],
        "",
        held.content[:4000],
    ]
    return "\n".join(lines)


def write_note(run_dir: Path, text: str) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / NOTE).write_text(text, encoding="utf-8")


def read_note(run_dir: Path) -> str:
    try:
        return (run_dir / NOTE).read_text(encoding="utf-8")
    except OSError:
        return ""
