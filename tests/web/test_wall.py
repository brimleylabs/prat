"""The agent wall: live tiles for running agents and a full-screen view of one."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from prat.config import Settings
from prat.db import Database, Project
from prat.domain import CardStatus
from prat.journal import Journal, read_first_and_last
from web_support import make_web

pytestmark = pytest.mark.integration


def wait_running(web: object, card_id: int) -> None:
    for _ in range(200):
        if web.status(card_id) == CardStatus.RUNNING:  # type: ignore[attr-defined]
            return
        time.sleep(0.05)
    raise AssertionError("card never started")


def test_running_agent_gets_a_tile_and_a_focus_view(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project) as web:
        empty = web.client.get("/wall").text
        assert "No agents running" in empty

        card_id = web.add_card(title="Paginate the items endpoint", ready=True)
        web.scripts[card_id] = [
            {"op": "text", "text": "Reading the router before adding pagination."},
            {"op": "bash", "command": "git push origin HEAD"},
            {"op": "wait", "seconds": 30},
            {"op": "finish"},
        ]
        web.post(f"/cards/{card_id}/run")
        wait_running(web, card_id)
        tiles = ""
        for _ in range(100):
            tiles = web.client.get("/wall/tiles").text
            if "Reading the router" in tiles and "denied" in tiles:
                break
            time.sleep(0.1)
        assert "Paginate the items endpoint" in tiles
        assert f'href="/wall/{card_id}"' in tiles
        assert "Reading the router before adding pagination." in tiles
        assert "1 denied" in tiles

        page = web.client.get("/wall").text
        assert 'hx-get="/wall/tiles"' in page and 'data-board-stream="/events/board"' in page

        focus = web.client.get(f"/wall/{card_id}").text
        assert 'id="live-log"' in focus
        assert f'data-stream="/events/cards/{card_id}"' in focus
        assert "Reading the router before adding pagination." in focus

        web.post(f"/cards/{card_id}/cancel")
        web.wait_for(card_id, CardStatus.CANCELLED, timeout=15)
        assert "No agents running" in web.client.get("/wall/tiles").text
        ended = web.client.get(f"/wall/{card_id}").text
        assert 'id="live-log"' not in ended and "This session has ended" in ended


def test_focus_on_an_unknown_card(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project) as web:
        response = web.client.get("/wall/999", headers={"Accept": "text/html"})
        assert response.status_code == 404


def test_reading_the_ends_of_a_long_journal(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    journal = Journal(path)
    for n in range(3000):
        journal.append("runtime.text", {"text": f"line {n} " + "x" * 200})
    first, last = read_first_and_last(path, 5, max_bytes=8 * 1024)
    assert first is not None and first.data["text"].startswith("line 0 ")
    assert [e.data["text"].split()[1] for e in last] == ["2995", "2996", "2997", "2998", "2999"]
    assert read_first_and_last(tmp_path / "missing.jsonl", 5) == (None, [])
    assert datetime.now(UTC) - last[-1].ts < timedelta(minutes=5)
