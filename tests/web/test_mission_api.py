"""Mission control's API: another PRAT reads the snapshot; the terminal acts through the server.
Both take the access token as a bearer token and nothing else."""

from __future__ import annotations

import time

from conftest import add_card
from prat import store
from prat.config import Settings
from prat.db import Database, Project
from prat.domain import CardStatus
from prat.mission import remote_deck
from prat.web.tokens import ensure_access_token
from web_support import make_web


def wait_status(
    db: Database, card_id: int, wanted: set[CardStatus], timeout: float = 20
) -> CardStatus:
    deadline = time.monotonic() + timeout
    while True:
        with db.session() as session:
            status = store.get_card(session, card_id).status
        if status in wanted or time.monotonic() > deadline:
            return status
        time.sleep(0.05)


def test_the_snapshot_needs_the_token_and_reads_back(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project, login=False) as web:
        token = ensure_access_token(web.settings)
        assert web.client.get("/api/mission").status_code == 401
        wrong = web.client.get("/api/mission", headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        answer = web.client.get("/api/mission", headers={"Authorization": f"Bearer {token}"})
        assert answer.status_code == 200
        deck = remote_deck("mac", answer.json())
        assert deck.error == "" and deck.flights == []


def test_a_card_is_sent_back_from_the_terminal_and_its_agent_runs_again(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project) as web:
        token = ensure_access_token(web.settings)
        headers = {"Authorization": f"Bearer {token}"}
        card_id = add_card(db, project, title="Add greet", ready=False)
        web.post(f"/cards/{card_id}/ready")
        assert wait_status(db, card_id, {CardStatus.REVIEW}) is CardStatus.REVIEW
        refused = web.client.post("/api/act/rework", json={"card_id": card_id, "feedback": "x"})
        assert refused.status_code in (401, 403)
        empty = web.client.post("/api/act/rework", json={"card_id": card_id}, headers=headers)
        assert empty.status_code == 409
        sent = web.client.post(
            "/api/act/rework", json={"card_id": card_id, "feedback": "say hello"}, headers=headers
        )
        assert sent.status_code == 200, sent.text
        assert wait_status(db, card_id, {CardStatus.REVIEW}) is CardStatus.REVIEW
        with db.session() as session:
            assert len(store.get_card(session, card_id).runs) == 2
        approved = web.client.post("/api/act/approve", json={"card_id": card_id}, headers=headers)
        assert approved.status_code == 200, approved.text
        assert wait_status(db, card_id, {CardStatus.DONE}) is CardStatus.DONE


def test_the_crew_pages_show_who_they_are_and_take_a_message(
    settings: Settings, db: Database, project: Project
) -> None:
    from prat import identity

    with make_web(settings, db, project) as web:
        identity.save(
            web.settings.home,
            identity.Identity(
                "python", "Mara", "I build the APIs.", "calm", "eng", 1.0, "I want it right."
            ),
        )
        page = web.client.get("/crew")
        assert page.status_code == 200 and "Mara" in page.text and "I build the APIs." in page.text
        agent = web.client.get("/crew/python")
        assert agent.status_code == 200 and "I want it right." in agent.text
        sent = web.post("/crew/python/talk", data={"message": "How was your day?"})
        assert sent.status_code == 303
        again = web.client.get("/crew/python")
        assert "How was your day?" in again.text


def test_an_idea_is_decided_on_the_ideas_page_and_put_in_place_end_to_end(
    settings: Settings, db: Database, project: Project
) -> None:
    from prat import ideas

    with make_web(settings, db, project) as web:
        home = web.settings.home
        token = ensure_access_token(web.settings)
        headers = {"Authorization": f"Bearer {token}"}
        idea = ideas.propose(
            home,
            "python",
            title="Greet people by name",
            problem="Every greeting card asked for the same name handling.",
            proposal="Add a greet helper that takes a name and use it everywhere.",
            kind="project",
            project=project.slug,
        )
        other = ideas.propose(
            home,
            "critic",
            title="Skip review on tiny cards",
            problem="Tiny cards wait for a review that never finds anything.",
            proposal="Let one-line cards land without the reviewer.",
        )
        page = web.client.get("/ideas")
        assert page.status_code == 200 and "Greet people by name" in page.text
        assert "2 waiting" in page.text
        assert "(2 waiting)" in web.client.get("/crew").text
        # Declining needs a reason, so its author can learn from it.
        no_reason = web.client.post(
            "/api/act/idea-decline", json={"idea": other.id}, headers=headers
        )
        assert no_reason.status_code == 409
        declined = web.client.post(
            "/api/act/idea-decline",
            json={"idea": other.id, "note": "Every card gets a review."},
            headers=headers,
        )
        assert declined.status_code == 200, declined.text
        accepted = web.post(
            f"/ideas/{idea.id}/decide",
            data={"decision": "accept", "note": "Yes.", "build": "1", "project": project.slug},
        )
        assert accepted.status_code == 303
        card_id = ideas.get(home, idea.id).work_card
        assert card_id is not None
        assert wait_status(db, card_id, {CardStatus.REVIEW}) is CardStatus.REVIEW
        approved = web.client.post("/api/act/approve", json={"card_id": card_id}, headers=headers)
        assert approved.status_code == 200, approved.text
        assert wait_status(db, card_id, {CardStatus.DONE}) is CardStatus.DONE
        assert ideas.get(home, idea.id).status == "in_place"
        assert ideas.get(home, other.id).status == "declined"
