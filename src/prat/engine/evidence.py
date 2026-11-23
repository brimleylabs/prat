"""A reviewer's rejection, backed by something that runs.

A reviewer reads; it cannot run anything, and a model that reads code rejects correct work
more often than one would like. Each wrong rejection costs a whole rebuild, and two in a row
is a card going round in circles. So when a reviewer rejects, it is asked for commands that
fail on the work as it stands (the one failing test, a one-line check), and PRAT runs them:

* a command that does fail backs the rejection, and the card goes back to its builder;
* with nothing that fails, the rejection is the reviewer's reading alone. By default the
  first such rejection of a card still goes back (one rebuild is cheap, and most are right);
  from the second on the card goes to a person, with the objections, instead of round again.

``rejections`` chooses: ``second`` (that default), ``evidence`` (every rejection needs a
failing command) or ``any`` (every rejection sends the card back, as before).

Commands are the reviewer's words, so they are held to the builder's own policy, run in the
card's worktree with PRAT's settings and keys removed, and whatever they change is undone.
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

OUTPUT_TAIL = 1500
# Exit codes that say the command itself was wrong, not the work.
NOT_RUN = (126, 127)
# What a test runner prints when it was called wrongly or found nothing to run: the command's
# fault, which says nothing about the work.
MISUSED = re.compile(
    r"(?im)^\s*(error: )?usage: |unrecognized arguments?|unknown (option|flag|argument)"
    r"|no such option|is not recognized as an internal or external command"
    r"|command not found|^no tests (ran|found|collected)|collected 0 items"
    r"|no tests? (were )?(found|executed)|file or directory not found"
)
# pytest's own codes for "used wrongly" and "nothing collected".
PYTEST_NOT_RUN = (4, 5)


@dataclass(frozen=True)
class Ran:
    command: str
    # None: refused, or it did not finish.
    exit_code: int | None
    seconds: float = 0.0
    output: str = ""
    refused: str = ""
    timed_out: bool = False

    @property
    def misused(self) -> bool:
        """The command ended in an error of its own making, not a finding about the work."""
        if self.exit_code in NOT_RUN:
            return True
        if self.exit_code in PYTEST_NOT_RUN and "pytest" in self.command:
            return True
        return self.exit_code not in (None, 0) and MISUSED.search(self.output) is not None

    @property
    def failed(self) -> bool:
        """The command ran and says the work is wrong."""
        return self.exit_code is not None and self.exit_code != 0 and not self.misused

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run(
    commands: Sequence[str],
    worktree: Path,
    *,
    allowed: Callable[[str], str | None],
    env: Mapping[str, str],
    timeout: float,
    limit: int,
) -> list[Ran]:
    """Run up to ``limit`` commands in ``worktree``. ``allowed`` returns why a command may not
    run, or ``None`` when it may."""
    ran: list[Ran] = []
    for command in [c.strip() for c in commands if c.strip()][:limit]:
        why = allowed(command)
        if why is not None:
            ran.append(Ran(command, None, refused=why))
            continue
        began = time.monotonic()
        try:
            done = subprocess.run(  # noqa: S602 - vetted by the policy, as the builder's are
                command,
                shell=True,
                cwd=worktree,
                env=dict(env),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            ran.append(Ran(command, None, round(time.monotonic() - began, 1), timed_out=True))
            continue
        except OSError as exc:
            ran.append(Ran(command, None, refused=str(exc)))
            continue
        output = ((done.stdout or "") + (done.stderr or "")).strip()[-OUTPUT_TAIL:]
        ran.append(Ran(command, done.returncode, round(time.monotonic() - began, 1), output))
    return ran


def backed(ran: Sequence[Ran]) -> bool:
    return any(r.failed for r in ran)


def needed(mode: str, attempt: int) -> bool:
    """Whether a rejection on this run of a card needs a failing command to send it back."""
    return mode == "evidence" or (mode == "second" and attempt > 1)


def note(ran: Sequence[Ran]) -> str:
    """Why a rejection went to a person, for the card."""
    if not ran:
        return (
            "The reviewer rejected this work and gave no command that fails on it, so its "
            "objections are its reading alone. A person should decide whether to send it back."
        )
    lines = [
        "The reviewer rejected this work, and none of the commands it gave fails on it. A "
        "person should decide whether to send it back."
    ]
    for r in ran:
        if r.refused:
            lines.append(f"- not run ({r.refused}): {r.command}")
        elif r.timed_out:
            lines.append(f"- did not finish: {r.command}")
        elif r.misused:
            lines.append(f"- could not be run (exit {r.exit_code}): {r.command}")
        else:
            lines.append(f"- passed: {r.command}")
    return "\n".join(lines)
