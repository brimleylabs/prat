"""PRAT's store: choosing SQLite or Dolt, and the managed Dolt server.

The server tests need a Dolt binary (PRAT_TEST_DOLT_BIN, or dolt on the PATH)
and skip with a clear reason without one.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prat.cli import app
from prat.config import Settings, load_settings
from prat.db.migrate import current_revision, head_revision, upgrade
from prat.dolt import store as store_module
from prat.dolt.server import DoltError, ManagedDolt, find_dolt, pid_alive
from prat.dolt.store import StoreKind, choose, has_dolt_store, open_store, sqlite_file

pytestmark = pytest.mark.integration

# Read before the per-test isolation removes PRAT_ variables.
DOLT_BIN = os.environ.get("PRAT_TEST_DOLT_BIN") or (str(find_dolt()) if find_dolt() else "")
needs_dolt = pytest.mark.skipif(not DOLT_BIN, reason="no Dolt binary (set PRAT_TEST_DOLT_BIN)")


def home(tmp_path: Path, **overrides: object) -> Settings:
    # The suite pins PRAT_STORE=sqlite; these tests are about choosing, so default to auto.
    overrides.setdefault("store", "auto")
    return load_settings(tmp_path / "home", **overrides)


def fake_dolt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    binary = tmp_path / "bin" / "dolt"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("", encoding="utf-8")
    monkeypatch.setattr(store_module, "find_dolt", lambda _configured=None: binary)
    return binary


# Choosing --------------------------------------------------------------------------


def test_a_database_url_wins(tmp_path: Path) -> None:
    settings = home(tmp_path, database_url="sqlite:///x.db", store="dolt")
    assert choose(settings) is StoreKind.EXTERNAL


def test_an_explicit_choice_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dolt(monkeypatch, tmp_path)
    assert choose(home(tmp_path, store="sqlite")) is StoreKind.SQLITE
    assert choose(home(tmp_path, store="dolt")) is StoreKind.DOLT


def test_a_fresh_home_picks_dolt_when_it_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_module, "find_dolt", lambda _configured=None: None)
    assert choose(home(tmp_path)) is StoreKind.SQLITE
    fake_dolt(monkeypatch, tmp_path)
    assert choose(home(tmp_path)) is StoreKind.DOLT


def test_an_existing_sqlite_installation_never_switches_by_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_dolt(monkeypatch, tmp_path)
    settings = home(tmp_path)
    settings.ensure_dirs()
    sqlite_file(settings).write_bytes(b"")
    assert choose(settings) is StoreKind.SQLITE


def test_an_existing_dolt_store_is_kept_even_without_the_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_module, "find_dolt", lambda _configured=None: None)
    settings = home(tmp_path)
    (settings.home / "store" / "prat" / ".dolt").mkdir(parents=True)
    assert has_dolt_store(settings)
    assert choose(settings) is StoreKind.DOLT
    with pytest.raises(DoltError, match="no dolt binary"):
        open_store(settings)


def test_the_sqlite_store_opens_and_migrates(tmp_path: Path) -> None:
    settings = home(tmp_path, store="sqlite")
    settings.ensure_dirs()
    store = open_store(settings)
    try:
        upgrade(store.db)
        assert current_revision(store.db) == head_revision()
        assert store.kind is StoreKind.SQLITE and not store.is_dolt
        assert sqlite_file(settings).exists()
    finally:
        store.close()


def test_pid_alive() -> None:
    assert pid_alive(os.getpid())
    assert not pid_alive(0)


# The managed server ---------------------------------------------------------------


def port_closed(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) != 0


@needs_dolt
def test_the_managed_server_starts_migrates_and_stops(tmp_path: Path) -> None:
    import pymysql

    server = ManagedDolt(Path(DOLT_BIN), tmp_path / "store")
    info = server.start()
    try:
        assert server.owned
        store_url = server.url()
        assert "127.0.0.1" in store_url and "root" not in store_url
        from prat.db import Database

        db = Database(store_url)
        upgrade(db)
        assert current_revision(db) == head_revision()
        db.dispose()
        # No passwordless root on a server PRAT runs.
        with pytest.raises(pymysql.err.OperationalError):
            pymysql.connect(host="127.0.0.1", port=info.port, user="root", connect_timeout=3)
        # A second PRAT process reuses the running server.
        other = ManagedDolt(Path(DOLT_BIN), tmp_path / "store")
        assert other.start().port == info.port and not other.owned
        other.stop()
        assert not port_closed(info.port)
    finally:
        server.stop()
    assert port_closed(info.port)
    assert not (tmp_path / "store" / "server.json").exists()


@needs_dolt
def test_a_restart_keeps_the_data_and_the_lock_out(tmp_path: Path) -> None:
    import pymysql

    from prat.db import Database

    with ManagedDolt(Path(DOLT_BIN), tmp_path / "store") as first:
        db = Database(first.url())
        upgrade(db)
        db.dispose()
    with ManagedDolt(Path(DOLT_BIN), tmp_path / "store") as again:
        assert again.info is not None
        db = Database(again.url())
        assert current_revision(db) == head_revision()
        db.dispose()
        with pytest.raises(pymysql.err.OperationalError):
            pymysql.connect(host="127.0.0.1", port=again.info.port, user="root", connect_timeout=3)


@needs_dolt
def test_a_stale_state_file_is_recovered(tmp_path: Path) -> None:
    data = tmp_path / "store"
    data.mkdir(parents=True)
    (data / "server.json").write_text(
        '{"pid": 999999, "port": 1, "started_at": 0}', encoding="utf-8"
    )
    with ManagedDolt(Path(DOLT_BIN), data) as server:
        assert server.owned and server.info is not None and server.info.port != 1


@needs_dolt
def test_the_cli_runs_on_a_dolt_store(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"PRAT_DOLT_PATH": DOLT_BIN, "PRAT_STORE": "dolt", "COLUMNS": "200"}

    def cli(*args: str) -> str:
        result = runner.invoke(app, ["--home", str(tmp_path / "home"), *args], env=env)
        assert result.exit_code == 0, result.output
        return result.output

    cli("init")
    assert has_dolt_store(load_settings(tmp_path / "home"))
    out = cli("store", "status")
    assert "dolt" in out.lower()
    assert "not set up" in cli("improve", "status")
