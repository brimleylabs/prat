"""Project configuration as code.

Each repository may carry ``.prat/project.toml``. It is validated with
pydantic and never loosens installation-wide limits: the effective autonomy
of a run is the lowest of the installation ceiling, the project file and the
card.

Two paths matter for every project:

``checkout_path``
    The person's own working copy. PRAT reads it once to create the agent
    clone and may fetch from it; no agent ever runs or writes there.
``agent_path``
    A separate clone that PRAT owns. Card worktrees branch from its default
    branch and approved cards are merged into it, locally.
"""

from __future__ import annotations

import re
import shlex
import sys
import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from prat import paths
from prat.domain import Autonomy
from prat.model_registry import validate_role_map

PROJECT_DIR = ".prat"
PROJECT_FILE = "project.toml"
PROJECT_FILE_REL = f"{PROJECT_DIR}/{PROJECT_FILE}"

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
GATE_NAMES = ("setup", "test", "lint", "format", "build")
GateName = Literal["setup", "test", "lint", "format", "build"]
# Replaced in any command argument by the interpreter that is running PRAT, so
# a project file can name "this Python" without knowing where it is installed.
PYTHON_PLACEHOLDER = "{python}"
CATEGORY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

# Paths every agent is denied regardless of the project file.
ALWAYS_PROTECTED: tuple[str, ...] = (".git", f"{PROJECT_DIR}/")
# File names treated as secrets for reads and writes.
DEFAULT_SECRET_PATHS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "id_dsa*",
    ".netrc",
    "_netrc",
    ".pypirc",
    ".npmrc",
    ".git-credentials",
    ".pgpass",
    ".my.cnf",
    ".vault-token",
    ".claude.json",
    ".credentials.json",
    "*.keystore",
    "*.jks",
)
SECRET_PATH_EXCEPTIONS: tuple[str, ...] = (".env.example", ".env.sample", ".env.template")


class ProjectFileError(ValueError):
    """Raised when ``.prat/project.toml`` is missing required data or is invalid."""


Command = str | list[str]


def command_argv(command: Command) -> list[str]:
    """Split a configured command into an argument list (no shell involved).

    Strings are split with POSIX rules, so use forward slashes or the list
    form for Windows paths that contain backslashes. ``{python}`` becomes the
    interpreter running PRAT; it is substituted after splitting, so a path
    with spaces or backslashes stays one argument.
    """
    argv = list(command) if isinstance(command, list) else shlex.split(command, posix=True)
    return [arg.replace(PYTHON_PLACEHOLDER, sys.executable) for arg in argv]


def command_text(command: Command) -> str:
    """The command as one line that splits back into :func:`command_argv`'s result.

    The interpreter path is written with forward slashes so the line reads the
    same under POSIX splitting rules on every platform.
    """
    python = Path(sys.executable).as_posix()
    if isinstance(command, str):
        return command.replace(PYTHON_PLACEHOLDER, shlex.quote(python))
    return shlex.join(arg.replace(PYTHON_PLACEHOLDER, python) for arg in command)


class Commands(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setup: Command | None = None
    test: Command | None = None
    lint: Command | None = None
    format: Command | None = None
    build: Command | None = None

    @field_validator("setup", "test", "lint", "format", "build")
    @classmethod
    def _non_empty(cls, value: Command | None) -> Command | None:
        if value is None:
            return None
        argv = command_argv(value)
        if not argv or not argv[0].strip():
            raise ValueError("command must not be empty")
        return value

    def get(self, name: str) -> Command | None:
        value: Command | None = getattr(self, name, None)
        return value

    def defined(self) -> dict[str, Command]:
        return {name: cmd for name in GATE_NAMES if (cmd := self.get(name)) is not None}


class Verify(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gates: list[GateName] = Field(default_factory=list)
    timeout_seconds: Annotated[float, Field(gt=0, le=24 * 3600)] = 900.0


class ChangeRule(BaseModel):
    """One project rule for the change classifier (``[[changes.rule]]``)."""

    model_config = ConfigDict(extra="forbid")

    paths: Annotated[list[str], Field(min_length=1, max_length=50)]
    category: str
    risk: Literal["low", "medium", "high"] = "medium"

    @field_validator("category")
    @classmethod
    def _category_name(cls, value: str) -> str:
        if not CATEGORY_RE.match(value):
            raise ValueError(
                f"invalid category {value!r}: use lowercase letters, digits and underscores"
            )
        return value

    @field_validator("paths")
    @classmethod
    def _rule_patterns(cls, values: list[str]) -> list[str]:
        return _clean_patterns(values)


class Changes(BaseModel):
    """Project additions to the change classifier.

    Rules add categories and raise risk; they never remove a built-in
    classification or lower a risk. ``require_person`` lists categories that
    always need a person's approval. It is empty unless a project sets it.
    """

    model_config = ConfigDict(extra="forbid")

    rule: list[ChangeRule] = Field(default_factory=list, max_length=100)
    require_person: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("require_person")
    @classmethod
    def _category_names(cls, values: list[str]) -> list[str]:
        for value in values:
            if not CATEGORY_RE.match(value):
                raise ValueError(f"invalid category {value!r} in require_person")
        return values


REVIEWERS = ("criteria", "security")


class Review(BaseModel):
    """Which reviewers judge a change (``[review]``).

    ``criteria`` checks the change against the card; ``security`` checks that it
    does not make anything less safe. The criteria reviewer is always on. PRAT's
    own repository always has the security reviewer, whatever this says.
    """

    model_config = ConfigDict(extra="forbid")

    reviewers: list[str] = Field(default_factory=lambda: ["criteria"], max_length=2)

    @field_validator("reviewers")
    @classmethod
    def _known(cls, values: list[str]) -> list[str]:
        unknown = sorted(set(values) - set(REVIEWERS))
        if unknown:
            raise ValueError(f"unknown reviewers {unknown}; use {list(REVIEWERS)}")
        if "criteria" not in values:
            raise ValueError("the criteria reviewer cannot be turned off")
        return [name for name in REVIEWERS if name in values]


ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
DB_LABEL = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class DatabaseWorkspace(BaseModel):
    """A database the project's code uses (``[[workspace.database]]``).

    Each card gets its own branch of it next to its git branch (Dolt and Doltgres), or its own
    copy of it (plain PostgreSQL, the fallback for an application Doltgres cannot run). Connection
    details never live in this file: ``url_env`` names the variable in PRAT's
    environment that holds PRAT's connection URL, and ``database_env`` and
    ``app_url_env`` name the project's own settings that PRAT points at the
    card's branch for the agent and the gates.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = "app"
    # dolt speaks the MySQL protocol, doltgres the Postgres one; both branch per card.
    # postgres is plain PostgreSQL: a copy per card, no data diff or merge.
    kind: Literal["dolt", "doltgres", "postgres"] = "dolt"
    url_env: str
    database_env: str | None = None
    app_url_env: str | None = None
    default_branch: Annotated[str, Field(min_length=1, max_length=200)] = "main"

    @field_validator("url_env", "database_env", "app_url_env")
    @classmethod
    def _env_name(cls, value: str | None) -> str | None:
        if value is not None and not ENV_NAME.match(value):
            raise ValueError(f"{value!r} is not an environment variable name")
        return value

    @field_validator("name")
    @classmethod
    def _label(cls, value: str) -> str:
        if not DB_LABEL.match(value):
            raise ValueError(f"database name {value!r}: use lowercase letters, digits, _")
        return value


class Workspace(BaseModel):
    """Versioned state a card works on besides the git worktree (``[workspace]``)."""

    model_config = ConfigDict(extra="forbid")

    database: list[DatabaseWorkspace] = Field(default_factory=list, max_length=5)

    @field_validator("database")
    @classmethod
    def _unique(cls, values: list[DatabaseWorkspace]) -> list[DatabaseWorkspace]:
        names = [value.name for value in values]
        if len(names) != len(set(names)):
            raise ValueError("each [[workspace.database]] needs its own name")
        return values


ENVIRONMENT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class EnvironmentLink(BaseModel):
    """A labelled link into a running environment. ``{port}`` is replaced."""

    model_config = ConfigDict(extra="forbid")

    label: Annotated[str, Field(min_length=1, max_length=60)]
    url: Annotated[str, Field(min_length=1, max_length=500)]

    @field_validator("url")
    @classmethod
    def _http(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"link {value!r} must start with http:// or https://")
        return value


class Environment(BaseModel):
    """A way to run the project (``[[environment]]``): started, stopped and watched by PRAT.

    ``start`` runs without a shell, in ``cwd`` (relative to the repository). ``{port}`` in the
    command, ``env`` values, ``health`` and links is replaced with the environment's port, which
    for a card preview is a free one PRAT picks. ``stop`` is optional: without it, PRAT stops the
    process and everything it started.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    label: Annotated[str, Field(max_length=60)] = ""
    start: Command
    stop: Command | None = None
    cwd: str = "."
    port: Annotated[int, Field(ge=1, le=65535)] | None = None
    env: dict[str, str] = Field(default_factory=dict)
    health: Annotated[str, Field(max_length=500)] | None = None
    ready_seconds: Annotated[float, Field(gt=0, le=600)] = 60.0
    # Whether a card in review can be previewed with this environment.
    preview: bool = True
    link: list[EnvironmentLink] = Field(default_factory=list, max_length=20)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not ENVIRONMENT_NAME_RE.match(value):
            raise ValueError(f"environment name {value!r}: lowercase letters, digits and dashes")
        return value

    @field_validator("cwd")
    @classmethod
    def _relative(cls, value: str) -> str:
        (cleaned,) = _clean_patterns([value])
        if cleaned.startswith("/") or ":" in cleaned:
            raise ValueError(f"cwd {value!r} must be relative to the repository")
        return cleaned

    @field_validator("env")
    @classmethod
    def _env_names(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            if not ENV_VAR_RE.match(key):
                raise ValueError(f"{key!r} is not an environment variable name")
        return value

    @field_validator("health")
    @classmethod
    def _health(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError("health must be an http:// or https:// URL")
        return value

    @property
    def title(self) -> str:
        return self.label or self.name


def _clean_patterns(values: list[str]) -> list[str]:
    cleaned = []
    for value in values:
        text = value.strip().replace("\\", "/")
        if not text:
            raise ValueError("path patterns must not be empty")
        if text.startswith("../") or "/../" in text or text == "..":
            raise ValueError(f"pattern {value!r} escapes the repository")
        cleaned.append(text)
    return cleaned


class SandboxSpec(BaseModel):
    """How this project's commands run when the installation uses the sandbox."""

    model_config = ConfigDict(extra="forbid")

    # A container image with the project's runtimes, e.g. "php:8.3-cli". It needs sh, and
    # socat for environments. Default: PRAT's own image (Python, Node and build tools).
    image: (
        Annotated[str, Field(max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*$")] | None
    ) = None


class ProjectSpec(BaseModel):
    """Validated contents of ``.prat/project.toml``."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=120)]
    default_branch: Annotated[str, Field(min_length=1, max_length=200)] = "main"
    autonomy: Autonomy = Autonomy.LOW
    commands: Commands = Field(default_factory=Commands)
    verify: Verify = Field(default_factory=Verify)
    protected_paths: list[str] = Field(default_factory=list)
    secret_paths: list[str] = Field(default_factory=list)
    max_diff_lines: Annotated[int, Field(ge=1)] = 5000
    max_diff_files: Annotated[int, Field(ge=1)] = 200
    # Short facts every agent should know (layout, conventions, gotchas).
    notes: Annotated[str, Field(max_length=4000)] = ""
    # Role to model overrides for this project, e.g. {builder = "claude-opus-5-5"}.
    models: dict[str, str] = Field(default_factory=dict)
    # Extra rules for the change classifier and categories that need a person.
    changes: Changes = Field(default_factory=Changes)
    # Which reviewers judge a change.
    review: Review = Field(default_factory=Review)
    # Databases branched per card next to the git worktree.
    workspace: Workspace = Field(default_factory=Workspace)
    # Ways to run the project, started and stopped from PRAT.
    environment: list[Environment] = Field(default_factory=list, max_length=20)
    # The container image the sandbox runs this project's commands in.
    sandbox: SandboxSpec = Field(default_factory=SandboxSpec)

    @field_validator("environment")
    @classmethod
    def _unique_environments(cls, values: list[Environment]) -> list[Environment]:
        names = [value.name for value in values]
        if len(names) != len(set(names)):
            raise ValueError("each [[environment]] needs its own name")
        return values

    @field_validator("models")
    @classmethod
    def _known_roles(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_role_map(value)

    @field_validator("default_branch")
    @classmethod
    def _branch_name(cls, value: str) -> str:
        bad = (" ", "..", "~", "^", ":", "\\", "?", "*", "[")
        if (
            value.startswith(("-", "/"))
            or value.endswith(("/", ".lock"))
            or any(b in value for b in bad)
        ):
            raise ValueError(f"invalid branch name {value!r}")
        return value

    @field_validator("protected_paths", "secret_paths")
    @classmethod
    def _patterns(cls, values: list[str]) -> list[str]:
        return _clean_patterns(values)

    @model_validator(mode="after")
    def _gates_have_commands(self) -> ProjectSpec:
        missing = [g for g in self.verify.gates if self.commands.get(g) is None]
        if missing:
            raise ValueError(f"verify.gates names commands that are not defined: {missing}")
        return self

    @property
    def all_protected(self) -> list[str]:
        return [*ALWAYS_PROTECTED, *self.protected_paths]

    @property
    def all_secret_paths(self) -> list[str]:
        return [*DEFAULT_SECRET_PATHS, *self.secret_paths]


def parse_spec(text: str, *, source: str = PROJECT_FILE_REL) -> ProjectSpec:
    try:
        data: dict[str, Any] = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ProjectFileError(f"{source}: invalid TOML: {exc}") from exc
    try:
        return ProjectSpec.model_validate(data)
    except ValidationError as exc:
        raise ProjectFileError(f"{source}: {exc}") from exc


def project_file_at(repo: Path) -> Path | None:
    """The project file in a working tree, or None when there is none."""
    file = repo / PROJECT_FILE_REL
    return file if file.is_file() else None


def show_project_file(git: Any, ref: str) -> Any:
    """``git show`` of the project file committed on ``ref``; callers check ``.ok``."""
    return git.run("show", f"{ref}:{PROJECT_FILE_REL}", check=False)


def load_spec(repo: Path, *, default_name: str | None = None) -> ProjectSpec:
    """Load the project file from a working tree, or defaults if it is absent."""
    file = project_file_at(repo)
    if file is not None:
        return parse_spec(file.read_text(encoding="utf-8"), source=str(file))
    return ProjectSpec(name=default_name or repo.name)


def validate_slug(slug: str) -> str:
    if not SLUG_RE.match(slug):
        raise ValueError(
            f"invalid slug {slug!r}: use lowercase letters, digits and dashes (max 63)"
        )
    return slug


def slugify(text: str, max_length: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = slug[:max_length].rstrip("-")
    return slug or "item"


def check_project_paths(checkout_path: Path, agent_path: Path) -> None:
    """The agent clone must be fully separate from the person's checkout."""
    if paths.is_within(agent_path, checkout_path) or paths.is_within(checkout_path, agent_path):
        raise paths.ProtectedPathError(
            "agent_path must not be inside the checkout (or the other way round)"
        )


def default_agent_path(checkout_path: Path) -> Path:
    """Sibling directory ``<checkout>-agent`` next to the person's checkout."""
    resolved = paths.resolve(checkout_path)
    return resolved.with_name(resolved.name + "-agent")


EXAMPLE_PROJECT_TOML = """\
# prat project file. Commit it to the repository's default branch.
name = "acme-api"
default_branch = "main"
# read_only, low, medium or high. The installation ceiling still applies.
autonomy = "low"
# Glob patterns (relative to the repo root) the agent may never write.
protected_paths = ["migrations/**", "*.lock"]
# Extra secret files the agent may never read or write.
secret_paths = []
max_diff_lines = 5000
max_diff_files = 200
# Facts every agent should know about this repository.
notes = "FastAPI service. Tests live in tests/, one file per module."

[commands]
setup = "python -m pip install -e ."
test = "python -m pytest -q"
lint = "python -m ruff check ."

[verify]
gates = ["test", "lint"]
timeout_seconds = 900

# Optional: extra rules for the change classifier. A rule can add a category
# or raise a risk; it cannot lower one.
# [[changes.rule]]
# paths = ["billing/**"]
# category = "security"
# risk = "high"
#
# Categories that always need a person's approval (empty by default).
# [changes]
# require_person = ["security", "dependencies"]
"""
