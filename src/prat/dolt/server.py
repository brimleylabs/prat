"""PRAT's own ``dolt sql-server``.

PRAT runs one Dolt SQL server per PRAT home, as a child process:

* loopback only, on a port the operating system picks;
* data under ``$PRAT_HOME/store`` (one Dolt database, ``prat``);
* a ``prat`` SQL user with a random password, kept in
  ``$PRAT_HOME/store/credentials.json`` (owner-only where the system allows),
  and no passwordless root user after the first start;
* a state file so a second PRAT process (the CLI while ``prat serve`` runs)
  connects to the running server instead of starting another on the same
  data, which Dolt would refuse;
* stopped together with its whole process tree when the PRAT process that
  started it exits.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TextIO

from prat.proc import ProcessTree, run_tree, spawn

DATABASE = "prat"
USER = "prat"
HOST = "127.0.0.1"
START_TIMEOUT_SECONDS = 60.0
STATE_FILE = "server.json"
CREDENTIALS_FILE = "credentials.json"
LOG_FILE = "server.log"


class DoltError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServerInfo:
    pid: int
    port: int
    started_at: float


def find_dolt(configured: Path | str | None = None) -> Path | None:
    """The Dolt binary: a configured path, else ``dolt`` on the PATH."""
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_file() else None
    found = shutil.which("dolt")
    return Path(found) if found else None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        # getattr keeps the type check the same on every operating system.
        kernel32: Any = getattr(ctypes, "windll").kernel32  # noqa: B009
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def port_open(port: int, host: str = HOST) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def free_port(host: str = HOST) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _write_private(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    with contextlib.suppress(OSError):
        temporary.chmod(0o600)  # best effort; Windows keeps the home folder's ACL
    temporary.replace(path)


class ManagedDolt:
    def __init__(
        self, binary: Path, data_dir: Path, *, start_timeout: float = START_TIMEOUT_SECONDS
    ):
        self.binary = binary
        self.data_dir = data_dir
        self.start_timeout = start_timeout
        self.tree: ProcessTree | None = None
        self.info: ServerInfo | None = None
        self.owned = False
        self._log: TextIO | None = None

    # Files ---------------------------------------------------------------------------

    @property
    def database_dir(self) -> Path:
        return self.data_dir / DATABASE

    @property
    def state_file(self) -> Path:
        return self.data_dir / STATE_FILE

    @property
    def credentials_file(self) -> Path:
        return self.data_dir / CREDENTIALS_FILE

    def password(self) -> str | None:
        try:
            data = json.loads(self.credentials_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        value = data.get("password") if isinstance(data, dict) else None
        return value if isinstance(value, str) and value else None

    def url(self) -> str:
        if self.info is None:
            raise DoltError("the Dolt server is not running")
        password = self.password()
        if password is None:
            raise DoltError(f"no credentials in {self.credentials_file}")
        from urllib.parse import quote

        return (
            f"mysql+pymysql://{USER}:{quote(password, safe='')}@{HOST}:{self.info.port}/{DATABASE}"
        )

    def _env(self) -> dict[str, str]:
        # Dolt keeps its global config under DOLT_ROOT_PATH; PRAT's stays inside the store.
        env = dict(os.environ)
        env["DOLT_ROOT_PATH"] = str(self.data_dir / ".dolt-root")
        return env

    # Lifecycle ------------------------------------------------------------------------

    def ensure_database(self) -> bool:
        """Create the Dolt database folder. Returns True when it was created."""
        if (self.database_dir / ".dolt").is_dir():
            return False
        self.database_dir.mkdir(parents=True, exist_ok=True)
        done = run_tree(
            [str(self.binary), "init", "--name", "prat", "--email", "prat@prat.invalid"],
            cwd=self.database_dir,
            env=self._env(),
            timeout=120,
        )
        if done.returncode != 0:
            raise DoltError(f"dolt init failed: {(done.stderr or done.stdout).strip()[-500:]}")
        return True

    def running(self) -> ServerInfo | None:
        """A server another PRAT process started for this store, if it still answers."""
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            info = ServerInfo(int(data["pid"]), int(data["port"]), float(data["started_at"]))
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if pid_alive(info.pid) and port_open(info.port):
            return info
        with contextlib.suppress(OSError):
            self.state_file.unlink()
        return None

    def start(self) -> ServerInfo:
        existing = self.running()
        if existing is not None:
            self.info, self.owned = existing, False
            return existing
        created = self.ensure_database()
        first = created or self.password() is None
        port = free_port()
        argv = [
            str(self.binary),
            "sql-server",
            "--host",
            HOST,
            "--port",
            str(port),
            "--data-dir",
            str(self.data_dir),
            "--loglevel",
            "warning",
        ]
        if not first:
            argv.append("--skip-root-user-initialization")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._log = (self.data_dir / LOG_FILE).open("a", encoding="utf-8")
        try:
            self.tree = spawn(
                argv,
                cwd=self.data_dir,
                env=self._env(),
                stdin=subprocess.DEVNULL,
                stdout=self._log,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except OSError as exc:
            self._close_log()
            raise DoltError(f"could not start {self.binary}: {exc}") from exc
        self.owned = True
        self.info = ServerInfo(self.tree.proc.pid, port, time.time())
        try:
            self._wait_until_ready(port)
            if first:
                self._create_user(port)
        except Exception:
            self.stop()
            raise
        _write_private(self.state_file, asdict(self.info))
        return self.info

    def _wait_until_ready(self, port: int) -> None:
        deadline = time.monotonic() + self.start_timeout
        while time.monotonic() < deadline:
            if self.tree is not None and self.tree.proc.poll() is not None:
                raise DoltError(f"dolt sql-server exited; see {self.data_dir / LOG_FILE}")
            if port_open(port):
                return
            time.sleep(0.2)
        raise DoltError(f"dolt sql-server did not answer within {self.start_timeout:.0f}s")

    def _create_user(self, port: int) -> None:
        """First start: a PRAT user with a random password, and no passwordless root."""
        import pymysql

        password = secrets.token_urlsafe(24)
        deadline = time.monotonic() + self.start_timeout
        while True:
            try:
                # User statements take effect at once; there is nothing to commit.
                connection = pymysql.connect(
                    host=HOST, port=port, user="root", connect_timeout=5, autocommit=True
                )
                break
            except pymysql.err.OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{DATABASE}`")
                cursor.execute(f"DROP USER IF EXISTS '{USER}'@'%'")
                # With parameters the driver formats the statement: '%%' is the host '%'.
                cursor.execute(f"CREATE USER '{USER}'@'%%' IDENTIFIED BY %s", (password,))
                cursor.execute(f"GRANT ALL PRIVILEGES ON `{DATABASE}`.* TO '{USER}'@'%'")
                # Saved before root goes, so PRAT can never lock itself out.
                _write_private(self.credentials_file, {"user": USER, "password": password})
                cursor.execute("DROP USER IF EXISTS 'root'@'localhost'")
        finally:
            connection.close()

    def stop(self) -> None:
        """Stop the server if this process started it. Safe to call more than once."""
        if self.owned and self.tree is not None:
            self.tree.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.tree.proc.wait(timeout=30)
            self.tree.close()
            with contextlib.suppress(OSError):
                if self.state_file.exists():
                    data = json.loads(self.state_file.read_text(encoding="utf-8"))
                    if self.info is not None and data.get("pid") == self.info.pid:
                        self.state_file.unlink()
        self.tree = None
        self.owned = False
        self.info = None
        self._close_log()

    def _close_log(self) -> None:
        if self._log is not None:
            self._log.close()
            self._log = None

    def __enter__(self) -> ManagedDolt:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()
