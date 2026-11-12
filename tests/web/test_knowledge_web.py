"""The knowledge page, its graph data, a person's changes, and activity being noticed."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import func, select

from prat.config import Settings
from prat.db import Database, Project
from prat.db.models import ActivityBucket
from prat.knowledge.entries import Entry, KnowledgeBase, Link, new_id
from web_support import ORIGIN, make_web

pytestmark = pytest.mark.integration


def seeded(settings: Settings, project: Project) -> tuple[KnowledgeBase, Entry, Entry]:
    kb = KnowledgeBase(settings.home, project.slug)
    kb.ensure()
    first = Entry(
        id=new_id(), kind="pitfall", summary="README must keep the title line", paths=["README.md"]
    )
    second = Entry(
        id=new_id(),
        kind="convention",
        summary="Docs are plain Markdown",
        links=[Link(first.id, "related")],
    )
    kb.save(first)
    kb.save(second)
    kb.commit("seed")
    return kb, first, second


def test_the_page_lists_entries_and_the_graph_has_nodes_and_links(
    settings: Settings, db: Database, project: Project
) -> None:
    _, first, second = seeded(settings, project)
    with make_web(settings, db, project) as web:
        page = web.client.get(f"/projects/{project.slug}/knowledge")
        assert page.status_code == 200
        assert first.summary in page.text and second.summary in page.text
        assert "/static/vendor/cytoscape-3.34.4.min.js" in page.text
        assert not re.search(r"<script>|style=\"", page.text)  # nothing inline, for the CSP

        graph = web.client.get(f"/projects/{project.slug}/knowledge/graph.json").json()
        ids = {n["id"] for n in graph["nodes"]}
        assert {first.id, second.id, "file:README.md"} <= ids
        relations = {(e["source"], e["target"], e["relation"]) for e in graph["edges"]}
        assert (first.id, "file:README.md", "about") in relations
        assert (second.id, first.id, "related") in relations

        version = web.client.get(f"/projects/{project.slug}/knowledge/version").json()["version"]
        assert version == graph["version"]

        detail = web.client.get(f"/projects/{project.slug}/knowledge/entries/{first.id}")
        assert detail.status_code == 200 and "README.md" in detail.text
        assert (
            web.client.get(f"/projects/{project.slug}/knowledge/entries/k0000000000").status_code
            == 404
        )


def test_a_person_adds_confirms_and_retires_entries(
    settings: Settings, db: Database, project: Project
) -> None:
    kb, first, _ = seeded(settings, project)
    with make_web(settings, db, project) as web:
        before = web.client.get(f"/projects/{project.slug}/knowledge/version").json()["version"]
        added = web.post(
            f"/projects/{project.slug}/knowledge/entries",
            {"kind": "command", "summary": "Build with make", "body": "", "paths": "Makefile"},
        )
        assert added.status_code in (200, 303)
        (person,) = [e for e in kb.entries() if e.author == "person"]
        assert person.confirmed and person.paths == ["Makefile"]

        web.post(f"/projects/{project.slug}/knowledge/entries/{first.id}/confirm", {})
        assert kb.get(first.id).confirmed  # type: ignore[union-attr]
        web.post(f"/projects/{project.slug}/knowledge/entries/{first.id}/retire", {})
        assert kb.get(first.id).status == "retired"  # type: ignore[union-attr]
        assert kb.history(first.id)[0].subject.startswith("Retired by a person")

        after = web.client.get(f"/projects/{project.slug}/knowledge/version").json()["version"]
        assert after != before  # the open page redraws

        bad = web.post(
            f"/projects/{project.slug}/knowledge/entries",
            {"kind": "rumour", "summary": "x", "body": "", "paths": ""},
        )
        assert bad.status_code in (200, 303)
        assert len([e for e in kb.entries() if e.author == "person"]) == 1


def test_changes_need_the_csrf_token(settings: Settings, db: Database, project: Project) -> None:
    _, first, _ = seeded(settings, project)
    with make_web(settings, db, project) as web:
        response = web.client.post(
            f"/projects/{project.slug}/knowledge/entries/{first.id}/retire",
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 403


def test_page_views_count_as_activity_but_polling_does_not(
    settings: Settings, db: Database, project: Project
) -> None:
    def signals() -> int:
        with db.session() as session:
            return int(session.scalar(select(func.coalesce(func.sum(ActivityBucket.count), 0))))

    with make_web(settings, db, project) as web:
        context = web.app.state.prat
        context._last_signal = 0.0
        start = signals()
        web.client.get("/board", headers={"HX-Request": "true"})
        assert signals() == start
        web.client.get("/board")
        assert signals() == start + 1
        web.client.get("/projects")  # within the minute: not counted again
        assert signals() == start + 1
