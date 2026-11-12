"""SQLAlchemy models.

Portable across SQLite and Postgres: generic column types only, enums stored
as strings, JSON via the generic ``JSON`` type. Status columns are guarded by
validators that call :func:`prat.domain.check_transition`, so no code path can
write an illegal status. No credentials are stored in these tables: a web
session is stored under a hash of its id, so a copy of the database cannot be
used to sign in.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates

from prat.domain import (
    Autonomy,
    CardKind,
    CardStatus,
    CommentAuthor,
    Complexity,
    CycleStage,
    CycleStatus,
    CycleTrigger,
    Effort,
    FindingSource,
    FindingStatus,
    RunStatus,
    Severity,
    StepName,
    StepStatus,
    check_transition,
)

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=False,
        length=32,
        values_callable=lambda cls: [member.value for member in cls],
        validate_strings=True,
    )


# MySQL and Dolt keep whole seconds unless told otherwise; ordering events that
# happen within one second needs microseconds.
UTC_DATETIME = DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {datetime: UTC_DATETIME, dict[str, Any]: JSON}  # noqa: RUF012


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    checkout_path: Mapped[str] = mapped_column(Text)
    agent_path: Mapped[str] = mapped_column(Text)
    default_branch: Mapped[str] = mapped_column(String(200))
    # Snapshot of the validated project file taken at registration time. The
    # copy on the default branch wins at run time when present.
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # True for the one project that is PRAT's own source (see prat.improve).
    is_self: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    cards: Mapped[list[Card]] = relationship(back_populates="project")


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text, default="")
    acceptance_criteria: Mapped[str] = mapped_column(Text, default="")
    complexity: Mapped[Complexity] = mapped_column(
        _enum(Complexity, "complexity"), default=Complexity.MEDIUM
    )
    kind: Mapped[CardKind] = mapped_column(
        _enum(CardKind, "card_kind"), default=CardKind.CHANGE, server_default="change"
    )
    status: Mapped[CardStatus] = mapped_column(_enum(CardStatus, "card_status"), index=True)
    # Optional per-card override. It can only lower the project's level.
    autonomy: Mapped[Autonomy | None] = mapped_column(_enum(Autonomy, "autonomy"), nullable=True)
    budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="cards")
    runs: Mapped[list[Run]] = relationship(back_populates="card", order_by="Run.id")
    comments: Mapped[list[Comment]] = relationship(back_populates="card", order_by="Comment.id")

    @validates("status")
    def _validate_status(self, _key: str, value: CardStatus) -> CardStatus:
        check_transition("card", self.status, CardStatus(value))
        return CardStatus(value)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[RunStatus] = mapped_column(_enum(RunStatus, "run_status"), index=True)
    # Outcome decided by a step; finalize copies it into ``status``.
    outcome: Mapped[RunStatus | None] = mapped_column(
        _enum(RunStatus, "run_outcome"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    runtime: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    autonomy: Mapped[Autonomy | None] = mapped_column(
        _enum(Autonomy, "run_autonomy"), nullable=True
    )
    budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    worktree_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    base_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    head_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    spec: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    journal_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    card: Mapped[Card] = relationship(back_populates="runs")
    steps: Mapped[list[RunStep]] = relationship(
        back_populates="run", order_by="RunStep.position", cascade="all, delete-orphan"
    )

    @validates("status")
    def _validate_status(self, _key: str, value: RunStatus) -> RunStatus:
        check_transition("run", self.status, RunStatus(value))
        return RunStatus(value)


class RunStep(Base):
    __tablename__ = "run_steps"
    __table_args__ = (UniqueConstraint("run_id", "name", name="run_step"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    name: Mapped[StepName] = mapped_column(_enum(StepName, "step_name"))
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[StepStatus] = mapped_column(_enum(StepStatus, "step_status"))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    run: Mapped[Run] = relationship(back_populates="steps")

    @validates("status")
    def _validate_status(self, _key: str, value: StepStatus) -> StepStatus:
        check_transition("step", self.status, StepStatus(value))
        return StepStatus(value)


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    author: Mapped[CommentAuthor] = mapped_column(_enum(CommentAuthor, "comment_author"))
    kind: Mapped[str] = mapped_column(String(32), default="note")
    body: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    card: Mapped[Card] = relationship(back_populates="comments")


class Event(Base):
    """Audit trail of state changes and notable actions."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    card_id: Mapped[int | None] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), nullable=True, index=True
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class WebSessionRecord(Base):
    """A signed-in browser session of the web UI.

    The cookie carries a random id; only its SHA-256 hash is stored here, so
    reading this table does not yield a usable session.
    """

    __tablename__ = "web_sessions"

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    # Fingerprint of the access token the session was created with.
    token_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(index=True)


class LoginNonce(Base):
    """A one-time login code that was used, kept until the code would expire anyway."""

    __tablename__ = "login_nonces"

    nonce_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)


class AppSetting(Base):
    """One installation setting that can change while PRAT runs (see prat.improve.settings)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ErrorLog(Base):
    """An error PRAT itself hit. Written only through :func:`prat.errors.record_error`,
    which caps and redacts the text before it gets here."""

    __tablename__ = "error_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Stable hash of source, kind and the normalised message or top frame.
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)


class ImproveCycle(Base):
    """One pass of the self-improvement loop: diagnose, research, select."""

    __tablename__ = "improve_cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[CycleTrigger] = mapped_column(_enum(CycleTrigger, "cycle_trigger"))
    status: Mapped[CycleStatus] = mapped_column(_enum(CycleStatus, "cycle_status"), index=True)
    stage: Mapped[CycleStage] = mapped_column(
        _enum(CycleStage, "cycle_stage"), default=CycleStage.DIAGNOSE
    )
    started_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImproveFinding(Base):
    """Something a cycle found worth changing in PRAT. The fingerprint keeps one
    row per subject however often it is found again."""

    __tablename__ = "improve_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The cycle that saw it first.
    cycle_id: Mapped[int | None] = mapped_column(
        ForeignKey("improve_cycles.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[FindingSource] = mapped_column(_enum(FindingSource, "finding_source"))
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    problem: Mapped[str] = mapped_column(Text, default="")
    proposal: Mapped[str] = mapped_column(Text, default="")
    acceptance_criteria: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    severity: Mapped[Severity] = mapped_column(
        _enum(Severity, "finding_severity"), default=Severity.MEDIUM
    )
    effort: Mapped[Effort] = mapped_column(_enum(Effort, "finding_effort"), default=Effort.MEDIUM)
    status: Mapped[FindingStatus] = mapped_column(
        _enum(FindingStatus, "finding_status"), default=FindingStatus.NEW, index=True
    )
    card_id: Mapped[int | None] = mapped_column(
        ForeignKey("cards.id", ondelete="SET NULL"), nullable=True, index=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImproveEvent(Base):
    """Audit trail of the self-improvement loop."""

    __tablename__ = "improve_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    cycle_id: Mapped[int | None] = mapped_column(
        ForeignKey("improve_cycles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    card_id: Mapped[int | None] = mapped_column(
        ForeignKey("cards.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ActivityBucket(Base):
    """How many times a person used PRAT in one local hour of one week (see prat.rhythm).
    Only counts are kept: never what was done."""

    __tablename__ = "activity_buckets"
    __table_args__ = (UniqueConstraint("week_start", "hour_of_week"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The Monday of the week, as YYYY-MM-DD in the person's time zone.
    week_start: Mapped[str] = mapped_column(String(10))
    hour_of_week: Mapped[int] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen: Mapped[datetime] = mapped_column(default=utcnow)


class Dream(Base):
    """One run of PRAT's quiet-hours work (see prat.dream)."""

    __tablename__ = "dreams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(20))
    window_start: Mapped[str] = mapped_column(String(40), default="", index=True)
    window_end: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(20), index=True)
    phase: Mapped[str] = mapped_column(String(20), default="")
    results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
