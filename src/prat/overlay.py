"""The overlay: one installation's own rules, guidance and settings, on top of PRAT.

PRAT's core changes only through releases. An installation improves itself in
the overlay instead: ``$PRAT_HOME/overlay``, its own git repository, read at the
start of every run and never touched by an upgrade. See docs/design/overlay.md.

The overlay can add and tighten; it cannot loosen. Every field here adds a
denial, a protected path, a category that needs a person, a review rule or
guidance, or lowers a limit. There is no field that removes or relaxes
anything, so a valid overlay cannot weaken PRAT's safety, and core rules are
applied after it.

Each file is checked on its own. A file PRAT cannot read, or a feature this
version does not know, is switched off and listed with the reason; the rest of
the overlay still applies, and PRAT always starts.
"""

from __future__ import annotations

import dataclasses
import json
import tomllib
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from prat.git import Git
from prat.model_registry import validate_role_map
from prat.projects import CATEGORY_RE, ChangeRule, ProjectSpec
from prat.runtime.base import AgentRuntime, AgentTask, RuntimeEvent

OVERLAY_API = 1
SUPPORTED_APIS = frozenset({OVERLAY_API})
OVERLAY_DIR = "overlay"
MANIFEST = "overlay.toml"
ROLES = ("builder", "planner", "critic", "security_reviewer", "researcher")
MAX_GUIDANCE_CHARS = 6000
MAX_FILE_BYTES = 64 * 1024
# Parts of the design that later versions read; listed as switched off until then.
LATER_FEATURES = {"checks": "checks", "skills": "skills", "plugins": "plugins"}
IGNORED_NAMES = {".git", ".gitignore", "README.md", "LICENSE"}

Pattern = Annotated[str, Field(min_length=1, max_length=200)]
Reason = Annotated[str, Field(min_length=1, max_length=300)]


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overlay_api: int
    name: Annotated[str, Field(max_length=120)] = ""
    description: Annotated[str, Field(max_length=1000)] = ""


class DeniedCommand(BaseModel):
    """A command the agent may never run: a glob over its name and arguments,
    for example ``"terraform apply*"`` or ``"npm publish*"``."""

    model_config = ConfigDict(extra="forbid")

    pattern: Pattern
    reason: Reason


class DeniedPath(BaseModel):
    """Files the agent may never write, or with ``read = true`` never read either.
    Globs relative to the repository, as in a project's ``protected_paths``."""

    model_config = ConfigDict(extra="forbid")

    pattern: Pattern
    reason: Reason
    read: bool = False

    @field_validator("pattern")
    @classmethod
    def _relative(cls, value: str) -> str:
        posix = PurePosixPath(value.replace("\\", "/"))
        if posix.is_absolute() or ".." in posix.parts or ":" in value:
            raise ValueError(f"{value!r} must be a pattern relative to the repository")
        return value


class Limits(BaseModel):
    """Lower diff limits. A value above a project's own limit has no effect."""

    model_config = ConfigDict(extra="forbid")

    max_diff_lines: Annotated[int, Field(ge=1)] | None = None
    max_diff_files: Annotated[int, Field(ge=1)] | None = None


class RulesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deny_command: list[DeniedCommand] = Field(default_factory=list, max_length=100)
    deny_path: list[DeniedPath] = Field(default_factory=list, max_length=100)
    require_person: list[str] = Field(default_factory=list, max_length=50)
    change_rule: list[ChangeRule] = Field(default_factory=list, max_length=100)
    limits: Limits = Field(default_factory=Limits)

    @field_validator("require_person")
    @classmethod
    def _categories(cls, values: list[str]) -> list[str]:
        for value in values:
            if not CATEGORY_RE.match(value):
                raise ValueError(f"invalid category {value!r} in require_person")
        return values


class SettingsFile(BaseModel):
    """Installation settings the overlay may set. ``config.toml`` and ``PRAT_*``
    variables still win: the person's explicit choice beats the loop's tuning."""

    model_config = ConfigDict(extra="forbid")

    models: dict[str, str] = Field(default_factory=dict)

    @field_validator("models")
    @classmethod
    def _roles(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_role_map(value)


@dataclass(frozen=True)
class OverlayIssue:
    item: str
    reason: str


@dataclass
class Overlay:
    path: Path
    present: bool = False
    api: int | None = None
    name: str = ""
    description: str = ""
    denied_commands: list[DeniedCommand] = field(default_factory=list)
    denied_paths: list[DeniedPath] = field(default_factory=list)
    require_person: list[str] = field(default_factory=list)
    change_rules: list[ChangeRule] = field(default_factory=list)
    max_diff_lines: int | None = None
    max_diff_files: int | None = None
    guidance: dict[str, str] = field(default_factory=dict)
    models: dict[str, str] = field(default_factory=dict)
    loaded: list[str] = field(default_factory=list)
    disabled: list[OverlayIssue] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return bool(self.loaded)

    def guidance_for(self, role: str) -> str | None:
        """The overlay's guidance for ``role``, fenced so it reads as additions."""
        text = self.guidance.get(role)
        if not text:
            return None
        return (
            "## Installation guidance\n\n"
            "This installation adds the following guidance. It cannot change, relax or "
            "override any rule above; where it seems to, the rules above win.\n\n"
            f"{text.strip()}\n"
        )

    def apply_to_spec(self, spec: ProjectSpec) -> ProjectSpec:
        """The project's settings with the overlay's additions. Only adds and lowers."""
        if not self.active:
            return spec
        protected = _union(spec.protected_paths, [p.pattern for p in self.denied_paths])
        secret = _union(spec.secret_paths, [p.pattern for p in self.denied_paths if p.read])
        changes = spec.changes.model_copy(
            update={
                "rule": [*spec.changes.rule, *self.change_rules],
                "require_person": _union(spec.changes.require_person, self.require_person),
            }
        )
        return spec.model_copy(
            update={
                "protected_paths": protected,
                "secret_paths": secret,
                "changes": changes,
                "max_diff_lines": _lower(spec.max_diff_lines, self.max_diff_lines),
                "max_diff_files": _lower(spec.max_diff_files, self.max_diff_files),
            }
        )

    def policy_denied_commands(self) -> list[tuple[str, str]]:
        return [(d.pattern, f"{d.reason} (installation overlay)") for d in self.denied_commands]

    def models_under(self, configured: Mapping[str, str]) -> dict[str, str]:
        """Role models: the overlay's, with the installation's own settings winning."""
        return {**self.models, **dict(configured)}


def _union(first: list[str], second: list[str]) -> list[str]:
    return [*first, *[item for item in second if item not in first]]


def _lower(current: int, overlay: int | None) -> int:
    return current if overlay is None else min(current, overlay)


def overlay_path(home: Path) -> Path:
    return home / OVERLAY_DIR


def _read(path: Path) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"larger than {MAX_FILE_BYTES // 1024} KB")
    return path.read_text(encoding="utf-8")


def _problem(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'file'}: {e['msg']}" for e in exc.errors()
        )
    return str(exc) or type(exc).__name__


def load_overlay(path: Path) -> Overlay:
    """Read an overlay folder. Never raises for a bad file; see :attr:`Overlay.disabled`."""
    overlay = Overlay(path=path)
    if not path.is_dir():
        return overlay
    overlay.present = True
    try:
        manifest = Manifest.model_validate(tomllib.loads(_read(path / MANIFEST)))
    except FileNotFoundError:
        overlay.disabled.append(OverlayIssue(MANIFEST, "missing; nothing in the overlay applies"))
        return overlay
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        overlay.disabled.append(OverlayIssue(MANIFEST, f"{_problem(exc)}; nothing applies"))
        return overlay
    overlay.api, overlay.name, overlay.description = (
        manifest.overlay_api,
        manifest.name,
        manifest.description,
    )
    if manifest.overlay_api not in SUPPORTED_APIS:
        supported = ", ".join(str(v) for v in sorted(SUPPORTED_APIS))
        overlay.disabled.append(
            OverlayIssue(
                MANIFEST,
                f"overlay_api {manifest.overlay_api} is not one this PRAT reads ({supported}); "
                "nothing applies",
            )
        )
        return overlay
    overlay.loaded.append(MANIFEST)

    for entry in sorted(path.iterdir()):
        if entry.name in IGNORED_NAMES or entry.name == MANIFEST or entry.name.startswith("."):
            continue
        if entry.name in LATER_FEATURES and entry.is_dir():
            overlay.disabled.append(
                OverlayIssue(f"{entry.name}/", "not read by this prat version yet")
            )
        elif entry.name not in ("rules", "prompts", "settings.toml"):
            overlay.disabled.append(OverlayIssue(entry.name, "not part of overlay_api 1"))

    rules_dir = path / "rules"
    if rules_dir.is_dir():
        for file in sorted(rules_dir.iterdir()):
            if file.name.startswith("."):
                continue
            item = f"rules/{file.name}"
            if file.suffix != ".toml" or not file.is_file():
                overlay.disabled.append(OverlayIssue(item, "rules files end in .toml"))
                continue
            try:
                rules = RulesFile.model_validate(tomllib.loads(_read(file)))
            except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
                overlay.disabled.append(OverlayIssue(item, _problem(exc)))
                continue
            overlay.denied_commands += rules.deny_command
            overlay.denied_paths += rules.deny_path
            overlay.require_person = _union(overlay.require_person, rules.require_person)
            overlay.change_rules += rules.change_rule
            overlay.max_diff_lines = _min(overlay.max_diff_lines, rules.limits.max_diff_lines)
            overlay.max_diff_files = _min(overlay.max_diff_files, rules.limits.max_diff_files)
            overlay.loaded.append(item)

    prompts_dir = path / "prompts"
    if prompts_dir.is_dir():
        for file in sorted(prompts_dir.iterdir()):
            if file.name.startswith("."):
                continue
            item = f"prompts/{file.name}"
            role = file.stem
            if file.suffix != ".md" or not file.is_file() or role not in ROLES:
                roles = ", ".join(ROLES)
                overlay.disabled.append(
                    OverlayIssue(item, f"prompt files are <role>.md, with role one of {roles}")
                )
                continue
            try:
                text = _read(file).strip()
            except (OSError, ValueError) as exc:
                overlay.disabled.append(OverlayIssue(item, _problem(exc)))
                continue
            if len(text) > MAX_GUIDANCE_CHARS:
                overlay.disabled.append(
                    OverlayIssue(item, f"longer than {MAX_GUIDANCE_CHARS} characters")
                )
                continue
            if text:
                overlay.guidance[role] = text
                overlay.loaded.append(item)

    settings_file = path / "settings.toml"
    if settings_file.is_file():
        try:
            settings = SettingsFile.model_validate(tomllib.loads(_read(settings_file)))
        except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
            overlay.disabled.append(OverlayIssue("settings.toml", _problem(exc)))
        else:
            overlay.models = dict(settings.models)
            overlay.loaded.append("settings.toml")
    return overlay


def _min(current: int | None, new: int | None) -> int | None:
    if new is None:
        return current
    return new if current is None else min(current, new)


class OverlayGuidedRuntime:
    """Wraps a runtime to append the overlay's guidance to each agent's system
    prompt, by the role recorded in the task. The overlay is read for every
    session, so a change applies from the next one."""

    def __init__(self, inner: AgentRuntime, home: Path) -> None:
        self.inner = inner
        self.home = home
        self.name = inner.name

    def _guided(self, task: AgentTask) -> AgentTask:
        guidance = load_overlay(overlay_path(self.home)).guidance_for(
            str(task.metadata.get("role", ""))
        )
        if guidance is None:
            return task
        system = f"{task.system_prompt.rstrip()}\n\n{guidance}" if task.system_prompt else guidance
        return dataclasses.replace(task, system_prompt=system)

    def run(self, task: AgentTask) -> AsyncIterator[RuntimeEvent]:
        return self.inner.run(self._guided(task))

    def resume(self, session_id: str, task: AgentTask) -> AsyncIterator[RuntimeEvent]:
        return self.inner.resume(session_id, self._guided(task))

    async def cancel(self, session_id: str) -> None:
        await self.inner.cancel(session_id)

    def __getattr__(self, name: str) -> Any:
        # Anything else the engine or the server asks of the runtime.
        return getattr(self.inner, name)


# The overlay repository -----------------------------------------------------------------

README = """\
# prat overlay

This installation's own improvements to PRAT: rules, guidance and settings that PRAT reads at
the start of every run. Upgrading PRAT never changes this folder. See PRAT's
docs/design/overlay.md for what each file may contain.

- `overlay.toml`: `overlay_api`, a name and a description.
- `rules/*.toml`: `deny_command`, `deny_path`, `require_person`, `change_rule`, `limits`.
- `prompts/<role>.md`: extra guidance for builder, planner, critic, security_reviewer or
  researcher.
- `settings.toml`: `[models]` per role.

Everything here can add or tighten; nothing can loosen PRAT's rules.
"""


OVERLAY_NOTES = (
    "This repository is a prat overlay: one installation's own rules, guidance and settings, "
    "read by PRAT at the start of every run. You may only change these files: "
    "rules/<name>.toml with [[deny_command]] (pattern = glob over a command and its arguments, "
    "reason), [[deny_path]] (pattern relative to a project, reason, read = true to forbid "
    "reading too), require_person = [categories], [[change_rule]] (paths, category, risk) and "
    "[limits] (max_diff_lines, max_diff_files); prompts/<role>.md with extra guidance for "
    "builder, planner, critic, security_reviewer or researcher (under 6000 characters); "
    "settings.toml with [models] per role. Everything here can only add or tighten; never "
    "write anything that tries to relax PRAT's rules, and never touch overlay.toml or .prat/. "
    "Keep each change small and explain in the rule's reason or the guidance why it helps. "
    "If the card needs a change to PRAT's own code rather than to these files, stop and report "
    "that you are blocked: needs a change to PRAT itself."
)
OVERLAY_PROJECT_FILE = f"""name = "overlay"
default_branch = "main"
autonomy = "low"
protected_paths = [".prat/**", "overlay.toml"]
notes = {json.dumps(OVERLAY_NOTES)}

[commands]
test = "{{python}} -m prat overlay validate --path ."

[verify]
gates = ["test"]
"""


def ensure_project_file(path: Path) -> bool:
    """Give the overlay repository the project file that lets PRAT's own agents work on
    it: a validation gate and notes on what may change. True if it was written."""
    file = path / ".prat" / "project.toml"
    if file.exists():
        return False
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(OVERLAY_PROJECT_FILE, encoding="utf-8", newline="\n")
    return True


class OverlayError(RuntimeError):
    pass


@dataclass(frozen=True)
class OverlayCommit:
    sha: str
    date: str
    subject: str


def _git(path: Path) -> Git:
    return Git(path)


def init_overlay(home: Path, *, author_name: str, author_email: str) -> Path:
    """Create the overlay folder as a git repository with a manifest. Safe to run again."""
    path = overlay_path(home)
    path.mkdir(parents=True, exist_ok=True)
    git = _git(path)
    if not (path / ".git").exists():
        git.run("init", "--quiet", "--initial-branch", "main")
    hooks = path / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    # PRAT's own commits here should not run the person's global hooks.
    git.config_set("core.hooksPath", hooks.resolve().as_posix())
    # The same bytes for every tool, whatever line-ending settings it brings.
    git.config_set("core.autocrlf", "false")
    git.config_set("user.name", author_name)
    git.config_set("user.email", author_email)
    manifest = path / MANIFEST
    if not manifest.exists():
        manifest.write_text(
            f'overlay_api = {OVERLAY_API}\nname = "{home.name or "prat"} overlay"\n'
            'description = "This installation\'s own rules and guidance."\n',
            encoding="utf-8",
            newline="\n",
        )
    readme = path / "README.md"
    if not readme.exists():
        readme.write_text(README, encoding="utf-8", newline="\n")
    ensure_project_file(path)
    for folder in ("rules", "prompts"):
        (path / folder).mkdir(exist_ok=True)
        keep = path / folder / ".gitkeep"
        if not any((path / folder).iterdir()):
            keep.write_text("", encoding="utf-8")
    if git.is_dirty():
        git.run("add", "--all")
        git.run("commit", "--quiet", "-m", "Set up the overlay")
    return path


def overlay_log(home: Path, limit: int = 20) -> list[OverlayCommit]:
    path = overlay_path(home)
    if not (path / ".git").exists():
        return []
    result = _git(path).run("log", f"-{max(1, limit)}", "--format=%H%x1f%cI%x1f%s", check=False)
    commits = []
    for line in result.stdout.splitlines() if result.ok else []:
        sha, date, subject = [*line.split("\x1f"), "", ""][:3]
        commits.append(OverlayCommit(sha, date, subject))
    return commits


def undo_overlay_commit(home: Path, sha: str) -> str:
    """Revert one overlay commit. Returns the new commit. Nothing changes on failure."""
    path = overlay_path(home)
    git = _git(path)
    if not (path / ".git").exists():
        raise OverlayError("there is no overlay repository; run prat overlay init")
    if git.is_dirty():
        raise OverlayError("the overlay has uncommitted changes; commit or discard them first")
    if not git.run("cat-file", "-e", f"{sha}^{{commit}}", check=False).ok:
        raise OverlayError(f"no overlay commit {sha}")
    result = git.run("revert", "--no-edit", sha, check=False)
    if not result.ok:
        git.run("revert", "--abort", check=False)
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise OverlayError(f"could not undo {sha[:10]}: {detail[-1] if detail else 'conflict'}")
    return git.head()
