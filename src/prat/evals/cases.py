"""Evaluation cases: a small repository, a card, and a hidden check the agent never sees.

A case is a folder::

    case.toml        the card (title, goal, acceptance criteria), the hidden check, and
                     the scripted reviewer's verdicts
    repo/            the starting files, including .prat/project.toml
    hidden/          files laid over the agent's work before the hidden check runs
    attempts/1.json  what the scripted builder does on the card's first run
    attempts/2.json  ... and on the next, after the card was sent back
    journals/        the recorded journals, for a case exported from a finished card

The attempts are scripts for the scripted runtime (prat.runtime.fake). A real agent
ignores them and works from the card. ``{python}`` in the project file and in the check
command becomes this machine's Python, or ``python3`` when commands run in the sandbox.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from prat.domain import Complexity, Verdict
from prat.runtime.fake import FakeScript

CASE_FILE = "case.toml"
PYTHON_PLACEHOLDER = "{python}"
SANDBOX_PYTHON = "python3"
CASE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class CaseError(ValueError):
    """A case folder that cannot be read or run."""


class Check(BaseModel):
    """The hidden check: a command run on the agent's work with the hidden files laid over
    it. Exit code 0 means the work does what the card asked."""

    model_config = ConfigDict(extra="forbid")

    command: list[str] = Field(min_length=1)
    timeout_seconds: Annotated[float, Field(gt=0, le=3600)] = 300.0


class ScriptedReview(BaseModel):
    """What the scripted reviewer says about one attempt."""

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict = Verdict.MET
    summary: str = "Scripted review."
    fix_list: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)


class RecordedRun(BaseModel):
    """How one run of an exported card went when it was recorded."""

    model_config = ConfigDict(extra="forbid")

    attempt: int
    run_id: int
    run_status: str
    reason: str = ""
    verdict: str = ""
    gates: dict[str, bool] = Field(default_factory=dict)
    denied: int = 0


class Recorded(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_id: int
    project: str
    exported_at: str
    runs: list[RecordedRun] = Field(default_factory=list)
    # Changed files the replay cannot write (binary or not UTF-8).
    skipped_files: list[str] = Field(default_factory=list)


class CaseSpec(BaseModel):
    """The contents of ``case.toml``."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=200)]
    goal: str = ""
    acceptance_criteria: str = ""
    complexity: Complexity = Complexity.LOW
    # Runs of the card before the case stops: the first, plus reworks.
    max_attempts: Annotated[int, Field(ge=1, le=10)] = 3
    check: Check | None = None
    scripted_review: list[ScriptedReview] = Field(default_factory=list)
    recorded: Recorded | None = None


@dataclass(frozen=True)
class EvalCase:
    name: str
    path: Path
    spec: CaseSpec
    attempts: tuple[FakeScript, ...]

    @property
    def repo(self) -> Path:
        return self.path / "repo"

    @property
    def hidden(self) -> Path:
        return self.path / "hidden"

    def review_for(self, index: int) -> ScriptedReview:
        """The scripted verdict for the ``index``-th review (from 0); the last one repeats,
        and a case without any is judged met."""
        reviews = self.spec.scripted_review
        if not reviews:
            return ScriptedReview()
        return reviews[min(index, len(reviews) - 1)]


def load_case(path: Path) -> EvalCase:
    path = path.resolve()
    name = path.name.lower()
    if not CASE_NAME.match(name):
        raise CaseError(f"{path.name}: a case folder name uses letters, digits and hyphens")
    file = path / CASE_FILE
    try:
        spec = CaseSpec.model_validate(tomllib.loads(file.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise CaseError(f"{path}: no {CASE_FILE}") from exc
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
        raise CaseError(f"{file}: {exc}") from exc
    if not (path / "repo").is_dir():
        raise CaseError(f"{path}: no repo/ folder with the starting files")
    attempts: list[FakeScript] = []
    folder = path / "attempts"
    if folder.is_dir():
        files = sorted(
            (f for f in folder.glob("*.json") if f.stem.isdigit()), key=lambda f: int(f.stem)
        )
        for number, script in enumerate(files, start=1):
            if int(script.stem) != number:
                raise CaseError(f"{folder}: attempts are numbered 1.json, 2.json and so on")
            try:
                attempts.append(FakeScript.load(script))
            except (OSError, ValueError) as exc:
                raise CaseError(f"{script}: {exc}") from exc
    return EvalCase(name, path, spec, tuple(attempts))


def load_cases(folder: Path, only: list[str] | None = None) -> list[EvalCase]:
    """The case in ``folder``, or every case folder directly inside it, by name."""
    folder = folder.expanduser()
    if not folder.is_dir():
        raise CaseError(f"{folder} is not a folder")
    if (folder / CASE_FILE).is_file():
        cases = [load_case(folder)]
    else:
        cases = [
            load_case(child)
            for child in sorted(folder.iterdir())
            if child.is_dir() and (child / CASE_FILE).is_file()
        ]
    if not cases:
        raise CaseError(f"no cases in {folder} (a case is a folder with {CASE_FILE})")
    if only:
        wanted = {name.lower() for name in only}
        missing = sorted(wanted - {case.name for case in cases})
        if missing:
            raise CaseError(f"no such case: {', '.join(missing)}")
        cases = [case for case in cases if case.name in wanted]
    return cases


def builtin_suite() -> Path:
    """The small suite that ships with PRAT."""
    return Path(__file__).resolve().parent / "suite"


def python_for(sandboxed: bool) -> str:
    return SANDBOX_PYTHON if sandboxed else Path(sys.executable).as_posix()


def fill_python(text: str, sandboxed: bool) -> str:
    return text.replace(PYTHON_PLACEHOLDER, python_for(sandboxed))
