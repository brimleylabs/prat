"""Settings for a PRAT installation.

Values come from, highest priority first: explicit overrides passed to
:func:`load_settings`, environment variables prefixed ``PRAT_``, the TOML file
``$PRAT_HOME/config.toml``, and the defaults below. ``database_url`` is the exception: it
is never read from the environment, because ``PRAT_DATABASE_URL`` names a project's
database (see :data:`NOT_FROM_ENVIRONMENT`).

``PRAT_HOME`` itself is resolved before anything else (argument, then the
``PRAT_HOME`` environment variable, then ``~/.prat``) because it decides where
the TOML file lives.
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from prat.domain import Autonomy
from prat.model_registry import validate_role_map

CONFIG_FILE_NAME = "config.toml"
DB_FILE_NAME = "prat.db"


class AuthMode(StrEnum):
    """How the Claude runtime authenticates.

    ``api_key``
        The default. An Anthropic API key from ``ANTHROPIC_API_KEY`` or the
        ``anthropic_api_key`` setting. Correct for anyone, including teams.
    ``claude_login``
        Opt in, single person only: the unmodified Claude Code binary uses the
        login its owner made with ``claude /login`` (or ``claude setup-token``).
        PRAT never reads, stores or forwards those credentials. See
        docs/SECURITY.md and the README before enabling it.
    """

    API_KEY = "api_key"
    CLAUDE_LOGIN = "claude_login"


def resolve_home(home: str | os.PathLike[str] | None = None) -> Path:
    """Return the absolute PRAT home directory (not created)."""
    if home is not None:
        raw = Path(home)
    elif os.environ.get("PRAT_HOME"):
        raw = Path(os.environ["PRAT_HOME"])
    else:
        raw = Path.home() / ".prat"
    return raw.expanduser().resolve()


# Settings never read from the environment. PRAT_DATABASE_URL is the variable a project's
# app, setup and checks read for the project's own database; a shell that has exported it
# must not move PRAT's records onto that database. PRAT's own address comes from config.toml.
NOT_FROM_ENVIRONMENT = frozenset({"database_url"})


class _InstallationEnv(EnvSettingsSource):
    """The ``PRAT_`` environment, less the names that belong to projects."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        if field_name in NOT_FROM_ENVIRONMENT:
            return None, field_name, False
        return super().get_field_value(field, field_name)


class Settings(BaseSettings):
    """Installation-wide settings. Project-level settings live in each repo."""

    model_config = SettingsConfigDict(
        env_prefix="PRAT_",
        extra="forbid",
        validate_default=True,
    )

    home: Path = Field(default_factory=resolve_home)
    database_url: str | None = None
    # Where PRAT keeps its records (see prat.dolt.store): "auto" keeps whatever this
    # home already uses and picks Dolt for a fresh home when Dolt is installed.
    store: Literal["auto", "sqlite", "dolt"] = "auto"
    # The dolt binary; default: dolt on the PATH.
    dolt_path: Path | None = None
    max_concurrent_runs: int = Field(default=6, ge=1, le=64)
    # Ceilings: under them the governor (prat.governor) holds a card back while starting it
    # would hurt (its parts not landed, rate limits, a full review queue, low memory, two cards
    # changing the same files) and says why.
    max_concurrent_per_project: int = Field(default=5, ge=1, le=64)
    # No new work while this many cards wait for review; unset = no limit.
    max_review_queue: int | None = Field(default=None, ge=1)
    # Free memory the governor keeps in reserve before starting another card.
    memory_reserve_mb: int | None = Field(default=1024, ge=0)
    # Hold new work once the service reports this share of a rate limit used; unset = only a
    # refusal holds it.
    pressure_hold: float | None = Field(default=0.95, gt=0, le=1)
    governor_poll_seconds: float = Field(default=5.0, gt=0)
    # An approved epic is built by a swarm (prat.swarm): its parts run at once on the epic's own
    # branch, each merged in as soon as its gates and reviewer pass, and the whole feature comes
    # to review once. Off: each part comes to review and lands on its own, as a card.
    swarm_epics: bool = True
    # A separate read-only planning session before the builder of a high-complexity card. Off:
    # the builder writes its plan as it starts, in the same session, so the code is read once.
    plan_pass: bool = False
    # Ceiling for every project and card. A project file cannot raise it.
    max_autonomy: Autonomy = Autonomy.MEDIUM
    default_budget_usd: float = Field(default=5.0, gt=0)
    gate_timeout_seconds: float = Field(default=900.0, gt=0)
    journal_payload_limit: int = Field(default=4000, ge=200)
    # A run whose heartbeat is older than this is considered abandoned.
    stale_run_seconds: float = Field(default=60.0, ge=0)
    heartbeat_seconds: float = Field(default=2.0, gt=0)
    git_author_name: str = "PRAT agent"
    git_author_email: str = "agent@prat.invalid"
    # Projects added from a repository link are cloned here (default: ~/prat-projects), each
    # next to its agent clone.
    projects_dir: Path | None = None
    # A Doltgres server (Postgres protocol) PRAT may create project databases on, for example
    # postgresql+psycopg://postgres:...@127.0.0.1:5480/postgres. The setup agent is offered a
    # database there, which branches per card. Never printed.
    project_database_url: SecretStr | None = None
    # A plain PostgreSQL server for projects whose application Doltgres cannot run: when a setup
    # card's checks fail on Doltgres and pass here, the project moves to it (a copy per card
    # instead of a branch). Never printed.
    fallback_database_url: SecretStr | None = None
    # A Dolt sql-server (MySQL protocol) for projects whose application runs on MySQL, for
    # example mysql+pymysql://root:...@127.0.0.1:3340/. Branches per card like Doltgres.
    # Never printed.
    project_mysql_url: SecretStr | None = None
    # Agent runtime ------------------------------------------------------------
    auth_mode: AuthMode = AuthMode.API_KEY
    # Prefer the ANTHROPIC_API_KEY environment variable; this setting exists for
    # services that cannot set one. It is never printed or written to the database.
    anthropic_api_key: SecretStr | None = None
    # Claude Code executable. Default: the one bundled with claude-agent-sdk.
    claude_cli_path: Path | None = None
    # Sandbox (prat.sandbox): "off" runs a project's commands on this machine; "docker" runs a
    # card's shell commands, gates, setup checks and environments in containers with no
    # network and no credentials (macOS and Linux).
    sandbox: Literal["off", "docker"] = "off"
    sandbox_image: str = "prat-sandbox:1"
    docker_path: str = "docker"
    # Limits for each container.
    sandbox_memory: str = Field(default="4g", pattern=r"^\d+[bkmg]?$")
    sandbox_cpus: float = Field(default=2.0, gt=0, le=256)
    # Role to model overrides, e.g. {builder = "claude-haiku-5-5"}.
    models: dict[str, str] = Field(default_factory=dict)
    # Loop detector: the same tool on the same target this many times within
    # the last ``loop_window`` calls is denied.
    loop_threshold: int = Field(default=5, ge=2, le=100)
    loop_window: int = Field(default=8, ge=2, le=200)
    # Lessons (prat.lessons): when a card reaches review, a short lesson for the engineer on
    # what was built and why. "review" writes it then, in the background; "off" only on request.
    lessons: Literal["review", "off"] = "review"
    # Alerts (prat.alerts): a URL each new alert is POSTed to as plain text (an ntfy topic
    # reaches a phone); a review waiting this many hours; an agent quiet this many minutes.
    alert_webhook: str | None = None
    alert_review_hours: float | None = Field(default=4.0, gt=0)
    alert_silent_minutes: float | None = Field(default=15.0, gt=0)
    # Other PRAT installations mission control shows on its deck, each
    # {name = "mac", url = "https://...", token = "..."} (the token from their access-token file).
    remotes: list[dict[str, str]] = Field(default_factory=list)
    # The crew's social hour (prat.social), after the night's dream: how many agents meet and
    # how many times each speaks. social_size = 0 turns it off.
    social_size: int = Field(default=5, ge=0, le=12)
    social_rounds: int = Field(default=3, ge=1, le=8)
    # What the crew calls the person they work for (when they say hello at the social hour).
    person_name: str | None = None
    # Each agent's work opens with its own story and the world it works in (prat.continuity):
    # "all", "off", or "half" (a fixed half of the agents, to measure whether it helps).
    continuity: Literal["all", "half", "off"] = "all"
    # With "half": the agents (keys: python, laravel, critic, ...) that get it; unset, a fixed
    # half by name.
    continuity_with: list[str] = Field(default_factory=list)
    # Web UI ---------------------------------------------------------------------
    # Loopback only by default. A non-loopback address also needs web_allow_remote.
    web_host: str = "127.0.0.1"
    web_port: int = Field(default=18300, ge=1, le=65535)
    web_allow_remote: bool = False
    # The address people type in the browser when it differs from host:port (for
    # example behind a reverse proxy). Origin and Host checks accept it.
    web_public_url: str | None = None
    web_session_hours: float = Field(default=12.0, gt=0, le=24 * 30)
    # Server-sent event streams end after this long; browsers reconnect by themselves.
    web_stream_seconds: float = Field(default=300.0, gt=0)
    web_poll_seconds: float = Field(default=0.5, gt=0, le=10)
    # The client (slug) the pages open on until someone picks another; unset shows all.
    default_client: str | None = None

    @field_validator("home", mode="after")
    @classmethod
    def _absolute_home(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("models", mode="after")
    @classmethod
    def _known_roles(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_role_map(value)

    @model_validator(mode="after")
    def _loop_window_fits(self) -> Settings:
        if self.loop_window < self.loop_threshold:
            raise ValueError("loop_window must be at least loop_threshold")
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            _InstallationEnv(settings_cls),
            TomlConfigSettingsSource(settings_cls),
        )

    # Derived locations -------------------------------------------------

    @property
    def config_file(self) -> Path:
        return self.home / CONFIG_FILE_NAME

    @property
    def sqlite_path(self) -> Path:
        return self.home / DB_FILE_NAME

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.sqlite_path.as_posix()}"

    @property
    def worktrees_dir(self) -> Path:
        return self.home / "worktrees"

    @property
    def journal_dir(self) -> Path:
        return self.home / "journal"

    @property
    def runs_dir(self) -> Path:
        return self.home / "runs"

    def run_dir(self, run_id: int) -> Path:
        return self.runs_dir / f"run-{run_id}"

    @property
    def access_token_file(self) -> Path:
        """The web UI access token. Created by ``prat init``, owner-only where possible."""
        return self.home / "access-token"

    def ensure_dirs(self) -> None:
        for path in (self.home, self.worktrees_dir, self.journal_dir, self.runs_dir):
            path.mkdir(parents=True, exist_ok=True)


def load_settings(home: str | os.PathLike[str] | None = None, **overrides: Any) -> Settings:
    """Load settings for the given home, reading ``config.toml`` if present."""
    home_path = resolve_home(home)
    toml_file = home_path / CONFIG_FILE_NAME

    class _FileSettings(Settings):
        model_config = SettingsConfigDict(toml_file=toml_file)

    return _FileSettings(home=home_path, **overrides)


DEFAULT_CONFIG_TOML = """\
# PRAT installation settings. Environment variables prefixed PRAT_ override these.
# Store: "auto" (Dolt when installed, for a fresh home; otherwise what the home has),
# "dolt" (PRAT runs its own Dolt server under this home) or "sqlite".
# store = "auto"
# dolt_path = "/usr/local/bin/dolt"
# PRAT's own records; set here only (PRAT_DATABASE_URL is a project's database, not PRAT's).
# database_url = "postgresql+psycopg://PRAT@localhost/PRAT"
max_concurrent_runs = 3
max_concurrent_per_project = 1
# Ceiling for every project and card: read_only, low, medium or high.
max_autonomy = "medium"
default_budget_usd = 5.0
gate_timeout_seconds = 900

# Claude runtime authentication: "api_key" (default; reads ANTHROPIC_API_KEY) or
# "claude_login" (personal use only: your own Claude Code login on your own
# plan; PRAT never touches the credentials). See the README before changing it.
auth_mode = "api_key"

# Sandbox: "docker" runs a card's commands, gates and environments in containers with no
# network and no credentials (macOS and Linux; build the image with `prat sandbox build`).
# sandbox = "off"
# sandbox_image = "prat-sandbox:1"

# Web UI (prat serve). Loopback only unless web_allow_remote is true; put TLS in front then.
# web_host = "127.0.0.1"
# web_port = 18300
# web_allow_remote = false
# web_public_url = "https://prat.example.org"
# web_session_hours = 12
# The client the pages open on (see prat client); without it they show every client.
# default_client = "acme"

# Model per role. Defaults: builder and critic Sonnet, planner Opus, helpers Haiku.
# [models]
# builder = "claude-sonnet-5-5"
# critic = "claude-sonnet-5-5"
"""
