"""Commits, tags, history and time travel for PRAT's own records on Dolt.

On a Dolt store every meaningful moment becomes a Dolt commit: each run step,
approve, rework, cancel, each self-improvement stage and landing, and each
settings change. The message is a short structured line (``run 41 step
verify: passed``) and the author is the actor (a person, the engine, a role),
so history can be filtered and read. On SQLite every call is a quiet no-op and
the read functions report that history needs the Dolt store.

Dolt shares a branch's working set between sessions once their SQL
transactions commit, so a commit made here captures every change the other
sessions have committed, under the message of the moment that triggered it.
"""

from __future__ import annotations

import logging
import re
import weakref
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from prat.db import Database

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 500
_SAFE_TAG = re.compile(r"[^A-Za-z0-9._-]+")
_COMMIT = re.compile(r"^[0-9a-v]{32}$")


class NeedsDolt(RuntimeError):
    """History, time travel and rollback need the Dolt store."""


@dataclass(frozen=True)
class Commit:
    hash: str
    author: str
    date: datetime
    message: str


_IS_DOLT: weakref.WeakKeyDictionary[Engine, bool] = weakref.WeakKeyDictionary()


def is_dolt(db: Database) -> bool:
    """Whether ``db`` is a Dolt server. Asked once per engine."""
    cached = _IS_DOLT.get(db.engine)
    if cached is not None:
        return cached
    answer = False
    if db.engine.dialect.name in ("mysql", "mariadb"):
        try:
            with db.engine.connect() as connection:
                connection.execute(text("SELECT dolt_version()")).scalar()
            answer = True
        except Exception:
            answer = False
    _IS_DOLT[db.engine] = answer
    return answer


def _author(actor: str) -> str:
    name = re.sub(r"[<>\n\r]+", " ", actor).strip() or "prat"
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "prat"
    return f"{name} <{slug}@prat.invalid>"


def commit(db: Database, message: str, *, actor: str = "engine") -> str | None:
    """Commit everything changed since the last commit. Returns the commit hash,
    or None on SQLite, when nothing changed, or when the commit fails (logged:
    the history must never break the work it records)."""
    if not is_dolt(db):
        return None
    line = " ".join(message.split())[:MAX_MESSAGE_CHARS] or "prat"
    try:
        with db.engine.connect() as connection:
            result = connection.execute(
                text("CALL DOLT_COMMIT('-A', '-m', :message, '--author', :author)"),
                {"message": line, "author": _author(actor)},
            ).scalar()
            connection.commit()
    except Exception as exc:
        if "nothing to commit" in str(exc).lower():
            return None
        log.warning("Dolt commit failed (%s): %s", line, exc)
        return None
    value = str(result or "")
    return value if _COMMIT.match(value) else None


def tag(db: Database, name: str, ref: str = "HEAD", *, message: str = "") -> str | None:
    """Tag a commit (``card-41-approved``). Returns the tag name, or None on SQLite."""
    if not is_dolt(db):
        return None
    safe = _SAFE_TAG.sub("-", name).strip("-")[:120]
    with db.engine.connect() as connection:
        args = ["-m", message] if message else []
        params: dict[str, Any] = {f"a{i}": value for i, value in enumerate([safe, ref, *args])}
        placeholders = ", ".join(f":a{i}" for i in range(len(params)))
        connection.execute(text(f"CALL DOLT_TAG({placeholders})"), params)
        connection.commit()
    return safe


def history(db: Database, *, limit: int = 50, grep: str | None = None) -> list[Commit]:
    """The newest commits first, optionally only those whose message contains ``grep``."""
    if not is_dolt(db):
        raise NeedsDolt("history needs the Dolt store")
    query = "SELECT commit_hash, committer, date, message FROM dolt_log"
    params: dict[str, Any] = {"limit": max(1, limit)}
    if grep:
        query += " WHERE message LIKE :grep"
        params["grep"] = f"%{grep}%"
    query += " ORDER BY date DESC LIMIT :limit"
    with db.engine.connect() as connection:
        rows = connection.execute(text(query), params).all()
    return [Commit(str(r[0]), str(r[1]), r[2], str(r[3])) for r in rows]


def changed_tables(db: Database, from_ref: str, to_ref: str = "HEAD") -> list[tuple[str, str]]:
    """(table, change) pairs between two commits, from ``dolt_diff``."""
    if not is_dolt(db):
        raise NeedsDolt("diffs need the Dolt store")
    with db.engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT COALESCE(to_table_name, from_table_name), diff_type "
                "FROM DOLT_DIFF_SUMMARY(:a, :b)"
            ),
            {"a": from_ref, "b": to_ref},
        ).all()
    return [(str(r[0]), str(r[1])) for r in rows]


def head(db: Database) -> str | None:
    if not is_dolt(db):
        return None
    with db.engine.connect() as connection:
        value = connection.execute(text("SELECT HASHOF('HEAD')")).scalar()
    return str(value) if value else None
