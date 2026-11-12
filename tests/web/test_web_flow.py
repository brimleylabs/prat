"""Pages render, and cards move through their life over HTTP with the fake runtime."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import DEFAULT_TOML, git
from prat import store
from prat.config import Settings
from prat.db import Database, Project
from prat.domain import CardStatus, Verdict
from prat.engine import FakeReviewer
from web_support import make_web

pytestmark = pytest.mark.integration

GREET = "def greet():\n    return 'hi'\n"


def test_pages_render(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project) as web:
        draft = web.add_card(title="Draft card", goal="A goal", acceptance_criteria="- one\n- two")
        pages = {
            "/board": "Board",
            "/board?project=acme-api": "acme-api",
            "/board/columns": "Draft card",
            "/cards/new": "New card",
            "/cards/new?project=acme-api": "Create card",
            f"/cards/{draft}": "Move to ready",
            f"/cards/{draft}/panel": "Draft card",
            "/projects": "acme-api",
            "/projects/new": "Repository",
            "/projects/acme-api": ".prat/project.toml",
            "/settings": "Model registry",
            "/usage": "No usage yet",
            "/usage?days=7": "7 days",
            "/usage?days=13": "30 days",
            "/login": "prat",
        }
        for path, expected in pages.items():
            response = web.client.get(path)
            assert response.status_code == 200, path
            assert expected in response.text, path
        for path in (
            "/cards/999",
            "/projects/nope",
            "/board?project=nope",
            f"/cards/{draft}/runs/9/replay",
        ):
            response = web.client.get(path, headers={"Accept": "text/html"})
            assert response.status_code == 404, path
            assert "Not found" in response.text


def test_create_run_review_and_approve(settings: Settings, db: Database, project: Project) -> None:
    reviewer = FakeReviewer(Verdict.MET, "Looks right.", fix_list=[])
    with make_web(settings, db, project, reviewer=reviewer) as web:
        response = web.post(
            "/cards",
            {
                "project": "acme-api",
                "title": "Add greet",
                "goal": "Add hello.greet()",
                "criteria": "- greet() returns 'hi'",
                "complexity": "low",
                "start": "1",
            },
        )
        assert response.status_code == 303
        card_id = int(response.headers["location"].rsplit("/", 1)[1])
        assert web.wait_for(card_id, CardStatus.REVIEW) == CardStatus.REVIEW

        page = web.client.get(f"/cards/{card_id}").text
        assert "Ready for your review" in page
        assert "Looks right." in page
        assert "hello.py" in page
        assert "return &#39;hi&#39;" in page
        assert "Approve and merge" in page
        assert "est." not in page and "$" not in page

        merged = web.post(f"/cards/{card_id}/approve", headers={"HX-Request": "true"})
        assert merged.status_code == 200
        assert merged.headers["HX-Redirect"] == f"/cards/{card_id}"
        assert web.status(card_id) == CardStatus.DONE
        agent = Path(project.agent_path)
        assert git(agent, "show", "main:hello.py").replace("\r\n", "\n") == GREET.strip()
        done = web.client.get(f"/cards/{card_id}").text
        assert "Merged into main" in done
        assert "git pull" in done


def test_draft_to_ready_starts_a_run(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project) as web:
        card_id = web.add_card()
        assert web.post(f"/cards/{card_id}/ready").status_code == 303
        assert web.wait_for(card_id, CardStatus.REVIEW) == CardStatus.REVIEW
        # Not allowed twice.
        web.post(f"/cards/{card_id}/ready")
        assert web.status(card_id) == CardStatus.REVIEW


def test_cancel_a_running_card(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project) as web:
        card_id = web.add_card(ready=True)
        web.scripts[card_id] = [{"op": "wait", "seconds": 30}, {"op": "finish"}]
        web.post(f"/cards/{card_id}/run")
        for _ in range(200):
            if web.status(card_id) == CardStatus.RUNNING:
                break
            time.sleep(0.05)
        page = web.client.get(f"/cards/{card_id}").text
        assert 'data-live="1"' in page
        assert "Cancel" in page
        assert web.post(f"/cards/{card_id}/cancel").status_code == 303
        assert web.wait_for(card_id, CardStatus.CANCELLED, timeout=15) == CardStatus.CANCELLED
        reopened = web.post(f"/cards/{card_id}/reopen")
        assert reopened.status_code == 303
        assert web.status(card_id) == CardStatus.DRAFT


def test_rework_through_http(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project) as web:
        card_id = web.add_card(ready=True)
        web.post(f"/cards/{card_id}/run")
        web.wait_for(card_id, CardStatus.REVIEW)

        # Feedback is required.
        web.post(f"/cards/{card_id}/rework", {"feedback": "  "})
        assert web.status(card_id) == CardStatus.REVIEW

        web.scripts[card_id] = [
            {"op": "write", "path": "hello.py", "content": "def greet():\n    return 'hello'\n"},
            {"op": "finish"},
        ]
        response = web.post(
            f"/cards/{card_id}/rework", {"feedback": "Say hello, not hi.", "run_now": "1"}
        )
        assert response.status_code == 303
        assert web.wait_for(card_id, CardStatus.REVIEW) == CardStatus.REVIEW
        with db.session() as session:
            card = store.get_card(session, card_id)
            assert len(card.runs) == 2
            assert card.runs[-1].feedback and "Say hello, not hi." in card.runs[-1].feedback
        page = web.client.get(f"/cards/{card_id}").text
        assert "Say hello, not hi." in page
        assert "Run 2" in page


def test_reviewer_sends_back_and_verdict_shows(
    settings: Settings, db: Database, project: Project
) -> None:
    reviewer = FakeReviewer(
        Verdict.NOT_MET, "Tests are missing.", fix_list=["Add a test for greet"]
    )
    with make_web(settings, db, project, reviewer=reviewer) as web:
        card_id = web.add_card(ready=True, acceptance_criteria="- tests cover greet")
        web.post(f"/cards/{card_id}/run")
        assert web.wait_for(card_id, CardStatus.REWORK) == CardStatus.REWORK
        page = web.client.get(f"/cards/{card_id}").text
        assert "Not met" in page
        assert "Add a test for greet" in page
        assert "Sent back for rework" in page
        assert "Run again" in page


def test_failed_gate_output_is_shown(
    settings: Settings,
    db: Database,
    project_factory: Callable[..., Project],
) -> None:
    failing = DEFAULT_TOML.replace("sys.exit(0)", "print('boom from gate'); sys.exit(3)")
    project = project_factory("gated", project_toml=failing)
    with make_web(settings, db, project) as web:
        card_id = web.add_card(ready=True)
        web.post(f"/cards/{card_id}/run")
        assert web.wait_for(card_id, CardStatus.FAILED) == CardStatus.FAILED
        page = web.client.get(f"/cards/{card_id}").text
        assert "The run failed" in page
        assert "boom from gate" in page
        assert "exit 3" in page


def test_comments_and_card_validation(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project) as web:
        bad = web.post("/cards", {"project": "acme-api", "title": ""})
        assert bad.status_code == 422
        assert "Give the card a title." in bad.text
        assert web.post("/cards", {"project": "nope", "title": "x"}).status_code == 422
        assert web.card_count() == 0

        card_id = web.add_card()
        assert (
            web.post(f"/cards/{card_id}/comments", {"body": "Remember the docs"}).status_code == 303
        )
        assert "Remember the docs" in web.client.get(f"/cards/{card_id}").text
        assert web.post("/cards/999/comments", {"body": "x"}).status_code == 404


def test_add_project_with_validation(
    settings: Settings,
    db: Database,
    project: Project,
    repo_factory: Callable[..., Path],
    tmp_path: Path,
) -> None:
    with make_web(settings, db, project) as web:
        cases = {
            "": "Enter the path",
            "relative/path": "absolute path",
            str(tmp_path / "missing"): "does not exist",
            str(tmp_path): "not a git checkout",
            project.checkout_path: "already exists",
        }
        for checkout, message in cases.items():
            response = web.post("/projects", {"checkout": checkout})
            assert response.status_code == 422, checkout
            assert message in response.text, checkout
        repo = repo_factory("widgets")
        inside = web.post("/projects", {"checkout": str(repo), "agent_path": str(repo / "agent")})
        assert inside.status_code == 422
        assert "inside the checkout" in inside.text
        bad_slug = web.post("/projects", {"checkout": str(repo), "slug": "Bad Slug"})
        assert bad_slug.status_code == 422
        ok = web.post("/projects", {"checkout": str(repo)})
        assert ok.status_code == 303
        assert ok.headers["location"] == "/projects/widgets"
        assert "widgets-agent" in web.client.get("/projects/widgets").text


def test_card_stream_delivers_events(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project) as web:
        card_id = web.add_card(ready=True)
        web.scripts[card_id] = [
            {"op": "text", "text": "Starting the work"},
            {"op": "wait", "seconds": 0.4},
            {"op": "bash", "command": "git push origin HEAD"},
            {"op": "write", "path": "hello.py", "content": GREET},
            {"op": "finish", "message": "done"},
        ]
        web.post(f"/cards/{card_id}/run")
        body = web.client.get(f"/events/cards/{card_id}").text
        events = [line[7:] for line in body.splitlines() if line.startswith("event: ")]
        assert "entry" in events
        assert events[-1] == "end"
        assert "Starting the work" in body
        assert "Denied" in body  # the push, highlighted as a policy denial
        assert "tone-denied" in body
        ids = [line[4:] for line in body.splitlines() if line.startswith("id: ")]
        assert ids and all(i.startswith("1:") for i in ids)
        seqs = [int(i.split(":")[1]) for i in ids]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        # Resuming after the last id sends nothing again.
        again = web.client.get(f"/events/cards/{card_id}", headers={"Last-Event-ID": ids[-1]}).text
        assert "event: entry" not in again


def test_board_stream_reports_changes(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project, web_stream_seconds=1.5) as web:
        timer = threading.Timer(0.4, lambda: web.add_card(title="Appears live"))
        timer.start()
        body = web.client.get("/events/board").text
        timer.join()
        assert "event: board" in body


def test_replay_filters(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project) as web:
        card_id = web.add_card(ready=True)
        web.scripts[card_id] = [
            {"op": "text", "text": "thinking"},
            {"op": "bash", "command": "git push"},
            {"op": "write", "path": "hello.py", "content": GREET},
            {"op": "finish"},
        ]
        web.post(f"/cards/{card_id}/run")
        web.wait_for(card_id, CardStatus.REVIEW)
        full = web.client.get(f"/cards/{card_id}/runs/1/replay").text
        assert "Session replay" in full
        assert "Tool use" in full
        denied = web.client.get(f"/cards/{card_id}/runs/1/replay?category=denied").text
        assert "Denied" in denied
        assert "thinking" not in denied
        fragment = web.client.get(
            f"/cards/{card_id}/runs/1/replay?category=text", headers={"HX-Request": "true"}
        ).text
        assert "thinking" in fragment
        assert "<html" not in fragment
