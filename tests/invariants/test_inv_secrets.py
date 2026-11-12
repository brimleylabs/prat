"""Invariant: secrets are redacted from journals, the error log and what the web UI shows."""

from __future__ import annotations

from pathlib import Path

from invariant_support import add_card, setup, web
from sqlalchemy import select

from prat import store
from prat.db.models import ErrorLog
from prat.domain import CommentAuthor
from prat.errors import record_error
from prat.journal import Journal

# Assembled at runtime; none of these is a real credential.
SECRETS = [
    "AKIA" + "Q" * 16,
    "gh" + "p_" + "y" * 36,
    "sk-" + "ant-" + "z" * 40,
]


def test_inv_journals_never_store_secrets(tmp_path: Path) -> None:
    path = tmp_path / "run-1.jsonl"
    Journal(path).append(
        "runtime.tool_result",
        {"output": "\n".join(f"key = {secret}" for secret in SECRETS), "nested": [SECRETS[0]]},
    )
    text = path.read_text(encoding="utf-8")
    for secret in SECRETS:
        assert secret not in text


def test_inv_the_error_log_never_stores_secrets(tmp_path: Path) -> None:
    s = setup(tmp_path)
    for secret in SECRETS:
        record_error(s.db, "engine", "Leak", f"failed with {secret}", detail={"value": secret})
    with s.db.session() as session:
        stored = " ".join(
            f"{row.message} {row.detail}" for row in session.scalars(select(ErrorLog))
        )
    for secret in SECRETS:
        assert secret not in stored


def test_inv_the_web_ui_never_shows_agent_written_secrets(tmp_path: Path) -> None:
    s = setup(tmp_path)
    card_id = add_card(s)
    with s.db.session() as session:
        card = store.get_card(session, card_id)
        store.add_comment(
            session,
            card,
            "Found these: " + " ".join(SECRETS),
            author=CommentAuthor.REVIEWER,
            kind="verdict",
        )
    with web(s) as (_app, client):
        page = client.get(f"/cards/{card_id}").text
    for secret in SECRETS:
        assert secret not in page
