"""The governor: whether a ready card may start now, and why not when it may not.

The run limits (``max_concurrent_runs``, ``max_concurrent_per_project``) are ceilings. Under
them the governor holds a card back while starting it would hurt, and says why, so a waiting
card never looks stuck. In the order it asks:

1. **Its parts are not ready**: a card that depends on others (an epic's part) waits until
   they have landed.
2. **Rate limits**: the service said no (``rejected``) and has not reset yet, or warned that
   usage is past ``pressure_hold`` of the limit.
3. **Your review queue**: with ``max_review_queue`` set, no new work starts while that many
   cards wait for you; finishing more than you can look at is not throughput.
4. **The machine**: less memory free than ``memory_reserve_mb``.
5. **Collisions**: another running card of the project is changing the same files (paths the
   card names, against what running cards have changed so far), or both cards write the
   project file (setup and infra cards).

:func:`machine` samples the computer itself (CPU, memory, the processes PRAT started) for the
governor and for mission control's machine view. Holds are recorded on the card
(``hold_reason``) while they last.
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import func, select

from prat.config import Settings
from prat.db import Card, Database, Run
from prat.domain import CardKind, CardStatus, RunStatus
from prat.runtime.base import PressureEvent

# Path-like words in a card's text: "api/routes.py", "src/app", "templates/", "Makefile".
_PATH = re.compile(
    r"(?<![\w./-])((?:[\w.-]+/)+[\w.-]*|[\w-]+\.(?:py|ts|tsx|js|jsx|php|go|rb|rs|sql|css|scss|html|vue|svelte|md|toml|yaml|yml|json))(?![\w/-])"
)
WRITES_PROJECT_FILE = (CardKind.SETUP, CardKind.INFRA)


# Rate limit pressure (kept for the whole process) -------------------------------------


@dataclass
class Pressure:
    status: str = "allowed"
    utilization: float | None = None
    resets_at: float | None = None
    kind: str = ""
    seen: float = 0.0


_pressure = Pressure()
_pressure_lock = threading.Lock()


def record_pressure(event: PressureEvent) -> None:
    with _pressure_lock:
        _pressure.status = event.status
        _pressure.utilization = event.utilization
        _pressure.resets_at = event.resets_at
        _pressure.kind = event.kind
        _pressure.seen = time.time()


def pressure() -> Pressure:
    with _pressure_lock:
        return Pressure(**_pressure.__dict__)


def clear_pressure() -> None:
    record_pressure(PressureEvent("allowed"))


# The machine ------------------------------------------------------------------------


@dataclass
class ProcessInfo:
    pid: int
    name: str
    cpu: float
    memory_mb: float
    cwd: str
    started: float


@dataclass
class Machine:
    cpu_percent: float
    memory_used_percent: float
    memory_available_mb: float
    memory_total_mb: float
    load: tuple[float, float, float] | None
    processes: list[ProcessInfo] = field(default_factory=list)


SERVER_PID_FILE = "server.pid"


def record_server(home: Path) -> None:
    """Called by ``prat serve``: other processes (mission control) find the server by it."""
    import os

    (home / SERVER_PID_FILE).write_text(str(os.getpid()), encoding="utf-8")


def server_pid(home: Path) -> int | None:
    """The running server's process id, or None when no server runs."""
    import psutil

    try:
        pid = int((home / SERVER_PID_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if psutil.pid_exists(pid) else None


def machine(*, processes: bool = False, root: int | None = None) -> Machine:
    """This computer now: CPU and memory, and with ``processes`` every process started by
    PRAT (``root``, the server, or else this process: its children, grandchildren and so on)
    with its working folder."""
    import psutil

    memory = psutil.virtual_memory()
    try:
        load: tuple[float, float, float] | None = psutil.getloadavg()
    except (AttributeError, OSError):
        load = None
    found = Machine(
        cpu_percent=psutil.cpu_percent(interval=None),
        memory_used_percent=memory.percent,
        memory_available_mb=memory.available / 2**20,
        memory_total_mb=memory.total / 2**20,
        load=load,
    )
    if processes:
        found.processes = descendants(root)
    return found


def descendants(pid: int | None = None) -> list[ProcessInfo]:
    import os

    import psutil

    try:
        root = psutil.Process(pid or os.getpid())
        children = root.children(recursive=True)
    except psutil.Error:
        return []
    found = []
    for child in children:
        try:
            with child.oneshot():
                cwd = ""
                with contextlib.suppress(psutil.Error):
                    cwd = child.cwd()
                found.append(
                    ProcessInfo(
                        pid=child.pid,
                        name=child.name(),
                        cpu=child.cpu_percent(interval=None),
                        memory_mb=child.memory_info().rss / 2**20,
                        cwd=cwd,
                        started=child.create_time(),
                    )
                )
        except psutil.Error:
            continue
    return found


def tree(pid: int) -> list[ProcessInfo]:
    """A process and everything under it (an environment's or a job's process)."""
    import psutil

    try:
        root = psutil.Process(pid)
        with root.oneshot():
            head = ProcessInfo(
                pid=root.pid,
                name=root.name(),
                cpu=root.cpu_percent(interval=None),
                memory_mb=root.memory_info().rss / 2**20,
                cwd="",
                started=root.create_time(),
            )
    except psutil.Error:
        return []
    return [head, *descendants(pid)]


# Collisions -------------------------------------------------------------------------


def paths_named(text: str) -> set[str]:
    """Files and folders a card's text names, as posix paths without a leading ./"""
    found = set()
    for match in _PATH.finditer(text or ""):
        path = match.group(1).strip("./")
        if path and not path.startswith(("http", "www")) and "//" not in path:
            found.add(path.rstrip("/"))
    return found


def overlap(named: set[str], touched: set[str]) -> str | None:
    """The first path in ``named`` that is, contains or sits in one of ``touched``."""
    for path in sorted(named):
        for other in touched:
            if path == other or other.startswith(path + "/") or path.startswith(other + "/"):
                return path
    return None


def touched_files(worktree: str | None, base: str | None) -> set[str]:
    """What a running card has changed so far: committed since its start, and not yet."""
    if not worktree or not Path(worktree).is_dir():
        return set()
    from prat.git import Git

    git = Git(Path(worktree))
    files: set[str] = set()
    if base:
        result = git.run("diff", "--name-only", base, check=False)
        if result.ok:
            files.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    status = git.run("status", "--porcelain", "--untracked-files=all", check=False)
    if status.ok:
        for line in status.stdout.splitlines():
            if len(line) > 3:
                files.add(line[3:].strip().split(" -> ")[-1])
    return {str(PurePosixPath(f)) for f in files}


# The governor -------------------------------------------------------------------------


class Governor:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db

    def check(self, card_id: int) -> str | None:
        """Why ``card_id`` should not start now, or None when it may. Runs git; call it in a
        thread."""
        with self.db.session() as session:
            card = session.get(Card, card_id)
            if card is None:
                return None
            waiting = self._dependencies(session, card)
            if waiting:
                return waiting
            reason = self._pressure() or self._review_queue(session) or self._memory()
            if reason:
                return reason
            return self._collision(session, card)

    @staticmethod
    def _dependencies(session: Any, card: Card) -> str | None:
        ids = [int(i) for i in card.depends_on or [] if str(i).isdigit()]
        if not ids:
            return None
        open_ids = [
            other.id
            for other in session.scalars(select(Card).where(Card.id.in_(ids)))
            if other.status != CardStatus.DONE
        ]
        if not open_ids:
            return None
        return "waits on " + ", ".join(f"#{i}" for i in sorted(open_ids)) + " to land"

    def _pressure(self) -> str | None:
        now = pressure()
        if now.status == "rejected" and (now.resets_at is None or now.resets_at > time.time()):
            when = (
                time.strftime("%H:%M", time.localtime(now.resets_at)) if now.resets_at else "soon"
            )
            return f"rate limit reached; it resets at {when}"
        limit = self.settings.pressure_hold
        if (
            limit is not None
            and now.utilization is not None
            and now.utilization >= limit
            and (now.resets_at is None or now.resets_at > time.time())
        ):
            return f"rate limit {now.utilization:.0%} used; holding new work until it resets"
        return None

    def _review_queue(self, session: Any) -> str | None:
        limit = self.settings.max_review_queue
        if not limit:
            return None
        waiting = session.scalar(
            select(func.count(Card.id)).where(Card.status == CardStatus.REVIEW)
        )
        if waiting and waiting >= limit:
            return f"{waiting} cards wait for your review (max_review_queue = {limit})"
        return None

    def _memory(self) -> str | None:
        reserve = self.settings.memory_reserve_mb
        if not reserve:
            return None
        try:
            free = machine().memory_available_mb
        except Exception:  # no reading, no hold
            return None
        if free < reserve:
            return f"memory headroom low: {free:.0f} MB free, {reserve} MB kept in reserve"
        return None

    def headroom(self) -> str:
        """One line on whether new work may start now, for mission control."""
        with self.db.session() as session:
            running = session.scalar(
                select(func.count(Card.id)).where(Card.status == CardStatus.RUNNING)
            )
            held = session.scalar(
                select(func.count(Card.id)).where(
                    Card.status.in_((CardStatus.READY, CardStatus.REWORK)),
                    Card.hold_reason.is_not(None),
                )
            )
            reason = self._pressure() or self._review_queue(session) or self._memory()
        if reason:
            return f"holding new work: {reason}"
        room = max(self.settings.max_concurrent_runs - int(running or 0), 0)
        text = (
            f"room for {room} more (running {running or 0} of {self.settings.max_concurrent_runs})"
        )
        if held:
            text += f"; {held} held for a reason of their own"
        return text

    def _collision(self, session: Any, card: Card) -> str | None:
        running = list(
            session.scalars(
                select(Card).where(
                    Card.project_id == card.project_id,
                    Card.status == CardStatus.RUNNING,
                    Card.id != card.id,
                )
            )
        )
        if not running:
            return None
        if card.kind in WRITES_PROJECT_FILE:
            for other in running:
                if other.kind in WRITES_PROJECT_FILE:
                    return f"#{other.id} is changing the project file too"
        named = paths_named(f"{card.title}\n{card.goal}\n{card.acceptance_criteria}")
        if not named:
            return None
        for other in running:
            run = session.scalar(
                select(Run)
                .where(Run.card_id == other.id, Run.status == RunStatus.RUNNING)
                .order_by(Run.id.desc())
            )
            touched = touched_files(
                run.worktree_path if run else None, run.start_commit if run else None
            )
            touched |= paths_named(f"{other.title}\n{other.goal}\n{other.acceptance_criteria}")
            hit = overlap(named, touched)
            if hit:
                return f"likely overlaps #{other.id} in {hit}"
        return None
