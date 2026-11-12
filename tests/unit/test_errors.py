from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import select

from prat.db import Database, ErrorLog
from prat.errors import (
    MAX_DETAIL_JSON_CHARS,
    MAX_MESSAGE_CHARS,
    fingerprint,
    normalise_message,
    record_error,
    redact_detail,
    redact_text,
)
from prat.policy.secrets import REDACTED, redact
from prat.projects import ProjectFileError, parse_spec

# Every sample is assembled at runtime; none is a real credential.
AWS_KEY = "AKIA" + "Z" * 16
GITHUB_TOKEN = "gh" + "p_" + "x" * 36
PRIVATE_KEY = (
    "-----BEGIN " + "RSA PRIVATE KEY-----\n" + "A" * 40 + "\n-----END " + "RSA PRIVATE KEY-----"
)


def rows(db: Database) -> list[ErrorLog]:
    with db.session() as session:
        return list(session.scalars(select(ErrorLog).order_by(ErrorLog.id)))


def test_scanner_redaction_replaces_known_formats() -> None:
    text = redact(f"key {AWS_KEY} and {GITHUB_TOKEN}\n{PRIVATE_KEY}\nend")
    assert AWS_KEY not in text
    assert GITHUB_TOKEN not in text
    assert "A" * 40 not in text
    assert text.count(REDACTED) == 3
    assert text.endswith("end")


@pytest.mark.parametrize(
    ("text", "hidden"),
    [
        ("connect to https://user:hunter2pass@example.invalid/db failed", "hunter2pass"),
        ("password=hunter2pass was refused", "hunter2pass"),
        ("API_KEY: 'abcd1234efgh'", "abcd1234efgh"),
        ('{"client_secret": "s3cr3tvalue"}', "s3cr3tvalue"),
        ("Authorization: Bearer abcdefgh12345678", "abcdefgh12345678"),
    ],
)
def test_redact_text_hides_credential_shaped_values(text: str, hidden: str) -> None:
    assert hidden not in redact_text(text)
    assert REDACTED in redact_text(text)


def test_redact_text_leaves_ordinary_text_alone() -> None:
    text = "FileNotFoundError: no such file 'settings.toml' in step verify"
    assert redact_text(text) == text


def test_redact_detail_walks_nested_values_and_secret_keys() -> None:
    detail = redact_detail(
        {
            "env": {"API_TOKEN": "plainvalue", "PATH": "/usr/bin"},
            "argv": ["tool", f"--key={AWS_KEY}"],
            "count": 3,
            "ok": False,
        }
    )
    text = json.dumps(detail)
    assert "plainvalue" not in text
    assert AWS_KEY not in text
    assert detail["env"]["PATH"] == "/usr/bin"
    assert detail["count"] == 3
    assert detail["ok"] is False


def test_record_error_stores_redacted_row(db: Database) -> None:
    error_id = record_error(
        db,
        "engine",
        "RuntimeError",
        f"upload failed with key {AWS_KEY}",
        detail={"token": GITHUB_TOKEN, "note": f"saw {GITHUB_TOKEN} in output"},
    )
    assert error_id is not None
    [row] = rows(db)
    assert row.source == "engine"
    assert row.kind == "RuntimeError"
    stored = row.message + json.dumps(row.detail)
    assert AWS_KEY not in stored
    assert GITHUB_TOKEN not in stored
    assert REDACTED in row.message
    assert len(row.fingerprint) == 64


def test_record_error_redacts_the_traceback_and_caps_sizes(db: Database) -> None:
    try:
        raise ValueError(f"bad value {AWS_KEY} " + "x" * 5000)
    except ValueError as exc:
        record_error(db, "web", type(exc).__name__, str(exc), exc=exc, detail={"big": "y" * 9000})
    [row] = rows(db)
    assert len(row.message) <= MAX_MESSAGE_CHARS
    assert AWS_KEY not in row.message
    assert len(json.dumps(row.detail)) <= MAX_DETAIL_JSON_CHARS
    assert isinstance(row.detail["traceback"], list)


def test_oversized_detail_is_replaced_by_its_keys(db: Database) -> None:
    detail: dict[str, Any] = {f"k{i}": "v" * 1900 for i in range(30)}
    record_error(db, "cli", "big", "too much detail", detail=detail)
    [row] = rows(db)
    assert row.detail["truncated"] is True
    assert "k0" in row.detail["keys"]


def test_fingerprint_ignores_values_that_vary() -> None:
    first = fingerprint("gate", "timeout", "gate 'test' timed out after 900 seconds (run 12)")
    second = fingerprint("gate", "timeout", "gate 'lint' timed out after 30 seconds (run 4711)")
    assert first == second
    assert first != fingerprint("gate", "timeout", "gate could not start")
    assert first != fingerprint("engine", "timeout", "gate 'test' timed out after 900 seconds")
    assert normalise_message("Card 12  FAILED at 0xDEADBEEF1") == "card # failed at #"


def test_fingerprint_uses_the_prat_frame_when_there_is_one() -> None:
    prints = []
    for text in ("name = ", "autonomy = [unclosed"):
        try:
            parse_spec(text)
        except ProjectFileError as exc:
            # Different messages, same place in PRAT: one fingerprint.
            prints.append(fingerprint("engine", type(exc).__name__, str(exc), exc))
    assert len(prints) == 2
    assert prints[0] == prints[1]
    assert prints[0] != fingerprint("engine", "ProjectFileError", "name = ")


def test_record_error_never_raises(db: Database) -> None:
    assert record_error(db, "nowhere", "kind", "unknown source") is None
    db.dispose()

    class Broken:
        def session(self) -> Any:
            raise RuntimeError("database is gone")

    assert record_error(Broken(), "engine", "kind", "message") is None  # type: ignore[arg-type]
