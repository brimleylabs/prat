"""Jobs: long-running work PRAT starts, watches and lands (``[[job]]`` in the project file).

A job is a crawl, a backfill, a batch of model calls: something that runs for minutes or hours
outside any agent. PRAT starts its command through :mod:`prat.jobrunner` (so how it ended is
known even across a PRAT restart), in the person's checkout or, when an agent starts it, in the
card's worktree, with:

* ``PRAT_PROGRESS``: a file the job appends one JSON line per update to (prat.progress);
* its database settings pointed at its own branch (``database = "branch"``, the default; for a
  card's job, the card's branch, so the data lands with the card), at the checkout's data
  (``default``) or at nothing (``none``).

From the progress lines PRAT works out, per phase, how far along the job is, its rate over the
last minutes (not the whole run: a scrape that slows down should move its ETA at once), an ETA
range, the error rate, and whether it has stalled (no progress for ``stall_minutes``). A job on
its own branch is landed (merged into the default branch) or discarded when it ends.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select

from prat import store
from prat.config import Settings
from prat.db import Card, Database, JobRun, Run
from prat.proc import kill_pid_tree, process_started_at, spawn
from prat.projects import Job, ProjectSpec, command_argv

JOBS_DIR = "jobs"
# Database branch numbers for jobs: no card reaches them.
JOB_INSTANCE_BASE = 2_000_000_000
RECENT_SECONDS = 600.0
SAME_PROCESS_SECONDS = 2.0
_trees: dict[int, Any] = {}


class JobError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def run_dir(home: Path, slug: str, run_id: int) -> Path:
    return home / JOBS_DIR / slug / str(run_id)


def find_job(spec: ProjectSpec, name: str) -> Job:
    for job in spec.job:
        if job.name == name:
            return job
    known = ", ".join(j.name for j in spec.job) or "none"
    raise JobError(f"no job {name!r} in the project file; known: {known}")


def _row(session: Any, run_id: int) -> JobRun:
    row = session.get(JobRun, run_id)
    if row is None:
        raise JobError(f"no job run {run_id}")
    return cast(JobRun, row)


def _stamp(value: datetime | None) -> float | None:
    from prat.db import as_utc

    moment = as_utc(value) if value is not None else None
    return moment.timestamp() if moment is not None else None


# Starting and stopping ------------------------------------------------------------------


def start(
    settings: Settings,
    db: Database,
    slug: str,
    name: str,
    *,
    card_id: int | None = None,
    workdir: Path | None = None,
) -> int:
    """Start a job; returns its run id. Raises JobError."""
    from prat.environments import checkout_environment_variables, committed_spec
    from prat.verification import gate_environment
    from prat.workspace.dolt_branch import providers_for

    checkout, spec, problem = committed_spec(db, slug)
    if problem is not None:
        raise JobError(problem)
    job = find_job(spec, name)
    base = workdir or checkout
    cwd = (base / job.cwd).resolve()
    if not cwd.is_dir():
        raise JobError(f"the job's folder {cwd} does not exist")
    with db.session() as session:
        project = store.get_project(session, slug)
        row = JobRun(
            project_id=project.id,
            card_id=card_id,
            name=job.name,
            status="running",
            command=command_argv(job.command),
            workdir=str(cwd),
            database=job.database if spec.workspace.database else "none",
            created_at=_now(),
        )
        session.add(row)
        session.flush()
        run_id = row.id
    folder = run_dir(settings.home, slug, run_id)
    folder.mkdir(parents=True, exist_ok=True)
    progress, log, status = folder / "progress.jsonl", folder / "output.log", folder / "status.json"
    progress.touch()
    variables = checkout_environment_variables(db, settings.home, slug)
    env: dict[str, str] = dict(variables)
    if spec.workspace.database and job.database != "default":
        if job.database == "branch":
            providers = providers_for(
                list(spec.workspace.database), environ={**os.environ, **variables}
            )
            for provider in providers:
                if card_id is not None:
                    provider.create(card_id)
                    env.update(provider.agent_env(card_id))
                else:
                    instance = JOB_INSTANCE_BASE + run_id
                    provider.create(instance)
                    env.update(provider.agent_env(instance))
        else:  # none: the job gets no database settings at all
            for database in spec.workspace.database:
                for key in (database.url_env, database.database_env, database.app_url_env):
                    if key:
                        env.pop(key, None)
    env.update(job.env)
    env["PRAT_PROGRESS"] = str(progress)
    env["PRAT_JOB_RUN"] = str(run_id)
    argv = [sys.executable, "-m", "prat.jobrunner", str(status), "--", *command_argv(job.command)]
    try:
        with log.open("ab") as out:
            tree = spawn(
                argv,
                cwd=cwd,
                env=gate_environment(env),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
            )
    except OSError as exc:
        _finish(db, run_id, "failed", reason=f"could not start: {exc}")
        raise JobError(f"could not start {job.title}: {exc}") from exc
    _trees[run_id] = tree
    with db.session() as session:
        row = _row(session, run_id)
        row.pid = tree.proc.pid
        row.started_at = _now()
        row.progress_path = str(progress)
        row.log_path = str(log)
        row.last = {"process_started": process_started_at(tree.proc.pid) or time.time()}
        store.record_event(
            session,
            "job.started",
            project_id=row.project_id,
            card_id=card_id,
            data={"job_run": run_id, "name": job.name},
        )
    return run_id


def start_for_agent(settings: Settings, db: Database, slug: str, name: str, card_id: int) -> str:
    """Start a job from a card's session, in the card's worktree. Returns what the agent reads."""
    with db.session() as session:
        run = session.scalar(
            select(Run).where(Run.card_id == card_id).order_by(Run.id.desc()).limit(1)
        )
        worktree = Path(run.worktree_path) if run and run.worktree_path else None
    try:
        run_id = start(settings, db, slug, name, card_id=card_id, workdir=worktree)
    except JobError as exc:
        return f"could not start the job: {exc}"
    return (
        f"Job {name} started as run {run_id}, writing to this card's database branch. It runs "
        "in the background: check it with job_status, do not wait in the shell."
    )


def stop(db: Database, run_id: int, reason: str = "stopped by request") -> None:
    with db.session() as session:
        row = session.get(JobRun, run_id)
        if row is None:
            raise JobError(f"no job run {run_id}")
        if row.status != "running":
            return
        pid, expected = row.pid, (row.last or {}).get("process_started")
    tree = _trees.pop(run_id, None)
    if tree is not None:
        tree.kill()
    elif pid and _same(pid, expected):
        kill_pid_tree(pid)
    _finish(db, run_id, "stopped", reason=reason)


def _same(pid: int, expected: Any) -> bool:
    started = process_started_at(pid)
    return (
        started is not None
        and isinstance(expected, int | float)
        and abs(started - float(expected)) <= SAME_PROCESS_SECONDS
    )


def _finish(
    db: Database, run_id: int, status: str, *, code: int | None = None, reason: str = ""
) -> None:
    with db.session() as session:
        row = session.get(JobRun, run_id)
        if row is None or row.status != "running":
            return
        row.status = status
        row.exit_code = code
        row.reason = reason or row.reason
        row.finished_at = _now()
        store.record_event(
            session,
            "job.finished",
            project_id=row.project_id,
            card_id=row.card_id,
            data={"job_run": run_id, "name": row.name, "status": status, "exit_code": code},
        )


def refresh(db: Database) -> list[int]:
    """Notice jobs that ended (their status file, or a process gone without one). Returns the
    run ids that changed."""
    changed = []
    with db.session() as session:
        running = list(session.scalars(select(JobRun).where(JobRun.status == "running")))
        rows = [(r.id, r.log_path, r.pid, (r.last or {}).get("process_started")) for r in running]
    for run_id, log_path, pid, expected in rows:
        status_file = Path(log_path).with_name("status.json") if log_path else None
        data = None
        if status_file is not None and status_file.is_file():
            with contextlib.suppress(OSError, ValueError):
                data = json.loads(status_file.read_text(encoding="utf-8"))
        if data is not None:
            code = int(data.get("exit_code", 1))
            _finish(db, run_id, "succeeded" if code == 0 else "failed", code=code)
            _trees.pop(run_id, None)
            changed.append(run_id)
        elif pid and not _same(pid, expected):
            _finish(db, run_id, "failed", reason="the job's process ended without a status")
            _trees.pop(run_id, None)
            changed.append(run_id)
    return changed


# Data -----------------------------------------------------------------------------------


def _providers(settings: Settings, db: Database, slug: str) -> list[Any]:
    from prat.environments import checkout_environment_variables, committed_spec
    from prat.workspace.dolt_branch import providers_for

    _checkout, spec, _problem = committed_spec(db, slug)
    variables = checkout_environment_variables(db, settings.home, slug)
    return providers_for(list(spec.workspace.database), environ={**os.environ, **variables})


def land(settings: Settings, db: Database, run_id: int) -> str:
    """Merge a finished job's own branch into the default branch of each database."""
    row = _ended_on_branch(db, run_id)
    instance = JOB_INSTANCE_BASE + run_id
    merged = []
    for provider in _providers(settings, db, row["slug"]):
        if provider.exists(instance):
            provider.commit(instance, f"job {row['name']} run {run_id}")
            _before, after = provider.merge(instance, f"Merge job {row['name']} run {run_id}")
            provider.discard(instance)
            merged.append(f"{provider.database}@{after}")
    _data_state(db, run_id, "landed")
    return ", ".join(merged) or "nothing to land"


def discard(settings: Settings, db: Database, run_id: int) -> None:
    _ended_on_branch(db, run_id)
    instance = JOB_INSTANCE_BASE + run_id
    with db.session() as session:
        slug = _row(session, run_id).project.slug
    for provider in _providers(settings, db, slug):
        with contextlib.suppress(Exception):
            provider.discard(instance)
    _data_state(db, run_id, "discarded")


def _ended_on_branch(db: Database, run_id: int) -> dict[str, Any]:
    with db.session() as session:
        row = session.get(JobRun, run_id)
        if row is None:
            raise JobError(f"no job run {run_id}")
        if row.status == "running":
            raise JobError("the job is still running; stop it or wait for it to end")
        if row.database != "branch" or row.card_id is not None:
            raise JobError("this job did not write to a branch of its own")
        if row.data_state:
            raise JobError(f"its data was already {row.data_state}")
        return {"slug": row.project.slug, "name": row.name}


def _data_state(db: Database, run_id: int, state: str) -> None:
    with db.session() as session:
        row = session.get(JobRun, run_id)
        if row is not None:
            row.data_state = state
            store.record_event(
                session,
                f"job.{state}",
                project_id=row.project_id,
                data={"job_run": run_id, "name": row.name},
            )


# Progress --------------------------------------------------------------------------------


@dataclass
class PhaseView:
    name: str
    done: float = 0.0
    total: float | None = None
    unit: str = "items"
    rate: float | None = None  # per second, over the last minutes
    eta_low: float | None = None  # seconds
    eta_high: float | None = None
    started: float | None = None
    last: float | None = None

    @property
    def fraction(self) -> float | None:
        return min(self.done / self.total, 1.0) if self.total else None


@dataclass
class ProgressView:
    phases: list[PhaseView] = field(default_factory=list)
    errors: int = 0
    error_rate: float | None = None  # errors per item over the last minutes
    note: str = ""
    item: str = ""
    last_at: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    lines: int = 0

    @property
    def current(self) -> PhaseView | None:
        return self.phases[-1] if self.phases else None


def read_progress(path: Path | str | None, *, now: float | None = None) -> ProgressView:
    """Everything the job reported, as phases with rate and ETA."""
    view = ProgressView()
    if not path:
        return view
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8", errors="replace").splitlines()
        mtime = target.stat().st_mtime
    except OSError:
        return view
    entries: list[dict[str, Any]] = []
    for line in raw:
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and "done" in data:
            entries.append(data)
    if not entries:
        return view
    # Lines without a time are spread evenly up to the file's last change.
    stamped = [float(e["ts"]) for e in entries if isinstance(e.get("ts"), int | float)]
    first_ts = stamped[0] if stamped else mtime - len(entries)
    for index, entry in enumerate(entries):
        if not isinstance(entry.get("ts"), int | float):
            entry["ts"] = first_ts + (mtime - first_ts) * (index + 1) / len(entries)
    phases: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        phases.setdefault(str(entry.get("phase") or ""), []).append(entry)
    for name, points in phases.items():
        lastp = points[-1]
        phase = PhaseView(
            name=name or "work",
            done=float(lastp.get("done") or 0),
            total=float(lastp["total"]) if isinstance(lastp.get("total"), int | float) else None,
            unit=str(lastp.get("unit") or "items"),
            started=float(points[0]["ts"]),
            last=float(lastp["ts"]),
        )
        phase.rate, phase.eta_low, phase.eta_high = _rate_and_eta(points, phase.total)
        view.phases.append(phase)
    view.phases.sort(key=lambda p: p.started or 0)
    last = entries[-1]
    view.errors = int(last.get("errors") or 0)
    view.note = str(next((e.get("note") for e in reversed(entries) if e.get("note")), "") or "")
    view.item = str(last.get("item") or "")
    view.last_at = float(last["ts"])
    view.tokens_in = int(last.get("tokens_in") or 0)
    view.tokens_out = int(last.get("tokens_out") or 0)
    view.model = str(last.get("model") or "")
    view.lines = len(entries)
    moment = now or time.time()
    recent = [e for e in entries if float(e["ts"]) >= moment - RECENT_SECONDS] or entries[-2:]
    if len(recent) >= 2:
        items = float(recent[-1].get("done") or 0) - float(recent[0].get("done") or 0)
        errors = int(recent[-1].get("errors") or 0) - int(recent[0].get("errors") or 0)
        if items > 0:
            view.error_rate = max(errors, 0) / items
    return view


def _rate_and_eta(
    points: list[dict[str, Any]], total: float | None
) -> tuple[float | None, float | None, float | None]:
    def rate_over(seconds: float) -> float | None:
        end = points[-1]
        window = [p for p in points if float(p["ts"]) >= float(end["ts"]) - seconds]
        if len(window) < 2:
            window = points[-2:] if len(points) >= 2 else []
        if len(window) < 2:
            return None
        span = float(window[-1]["ts"]) - float(window[0]["ts"])
        done = float(window[-1].get("done") or 0) - float(window[0].get("done") or 0)
        return done / span if span > 0 and done > 0 else None

    fast, slow = rate_over(120.0), rate_over(RECENT_SECONDS)
    rate = slow or fast
    if total is None or rate is None:
        return rate, None, None
    remaining = max(total - float(points[-1].get("done") or 0), 0.0)
    etas = [remaining / r for r in (fast, slow) if r]
    return rate, min(etas), max(etas)


def stalled(
    row_status: str,
    view: ProgressView,
    started: float | None,
    minutes: float,
    now: float | None = None,
) -> bool:
    if row_status != "running":
        return False
    moment = now or time.time()
    reference = view.last_at or started
    return reference is not None and moment - reference > minutes * 60


# Reading for people and agents ---------------------------------------------------------------


@dataclass
class JobState:
    run_id: int
    slug: str
    name: str
    title: str
    status: str
    card_id: int | None
    started: float | None
    finished: float | None
    exit_code: int | None
    database: str
    data_state: str | None
    progress: ProgressView
    stalled: bool
    pid: int | None
    reason: str | None
    log_tail: str = ""


def states(
    db: Database, *, slug: str | None = None, limit: int = 30, project_ids: set[int] | None = None
) -> list[JobState]:
    from prat.environments import committed_spec

    refresh(db)
    with db.session() as session:
        query = select(JobRun).order_by(JobRun.id.desc()).limit(limit)
        if slug is not None:
            query = query.where(JobRun.project_id == store.get_project(session, slug).id)
        rows = list(session.scalars(query))
        raw = [
            (
                r.id,
                r.project.slug,
                r.project_id,
                r.name,
                r.status,
                r.card_id,
                _stamp(r.started_at),
                _stamp(r.finished_at),
                r.exit_code,
                r.database,
                r.data_state,
                r.progress_path,
                r.log_path,
                r.pid,
                r.reason,
            )
            for r in rows
        ]
    found = []
    specs: dict[str, Any] = {}
    for (
        run_id,
        project,
        project_id,
        name,
        status,
        card_id,
        started,
        finished,
        code,
        database,
        data_state,
        progress_path,
        log_path,
        pid,
        reason,
    ) in raw:
        if project_ids is not None and project_id not in project_ids:
            continue
        if project not in specs:
            try:
                specs[project] = committed_spec(db, project)[1]
            except LookupError:
                specs[project] = None
        spec = specs[project]
        job = next((j for j in spec.job if j.name == name), None) if spec else None
        view = read_progress(progress_path)
        found.append(
            JobState(
                run_id=run_id,
                slug=project,
                name=name,
                title=job.title if job else name,
                status=status,
                card_id=card_id,
                started=started,
                finished=finished,
                exit_code=code,
                database=database,
                data_state=data_state,
                progress=view,
                stalled=stalled(status, view, started, job.stall_minutes if job else 10.0),
                pid=pid,
                reason=reason,
                log_tail=_tail(log_path),
            )
        )
    return found


def _tail(path: str | None, lines: int = 3) -> str:
    if not path:
        return ""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(size - 4096, 0))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])


def clock(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m{secs:02d}s"


def summary(state: JobState) -> str:
    phase = state.progress.current
    parts = [f"job run {state.run_id} {state.name}: {state.status}"]
    if state.stalled:
        parts.append("STALLED (no progress lately)")
    if phase is not None:
        done = f"{phase.done:.0f}" + (f" of {phase.total:.0f}" if phase.total else "")
        parts.append(f"{phase.name} {done} {phase.unit}")
        if phase.rate:
            parts.append(f"{phase.rate * 60:.1f} {phase.unit}/min")
        if phase.eta_low is not None:
            parts.append(f"ETA {clock(phase.eta_low)} to {clock(phase.eta_high)}")
    if state.progress.errors:
        parts.append(f"{state.progress.errors} errors")
    if state.status != "running" and state.exit_code is not None:
        parts.append(f"exit code {state.exit_code}")
    if state.reason:
        parts.append(state.reason)
    return "; ".join(parts)


def describe(db: Database, card_id: int, run_id: int | None) -> str:
    found = [
        s
        for s in states(db, limit=50)
        if (run_id is None and s.card_id == card_id) or s.run_id == run_id
    ]
    if not found:
        return "No job runs for this card."
    text = "\n".join(summary(s) for s in found[:5])
    tail = found[0].log_tail
    return text + (f"\nlast output:\n{tail}" if tail else "")


def card_title(db: Database, card_id: int | None) -> str:
    if card_id is None:
        return ""
    with db.session() as session:
        card = session.get(Card, card_id)
        return card.title if card else ""
