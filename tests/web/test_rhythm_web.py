"""The rhythm and dreams panel on the Improve page."""

from __future__ import annotations

import asyncio
import time

import pytest

from prat import rhythm
from prat.config import Settings
from prat.db import Database, Project
from prat.dream import recent_dreams
from web_support import make_web

pytestmark = pytest.mark.integration


def test_the_panel_shows_state_window_and_heatmap(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project) as web:
        assert 'hx-get="/improve/rhythm"' in web.client.get("/improve").text
        panel = web.client.get("/improve/rhythm", headers={"HX-Request": "true"})
        assert panel.status_code == 200
        assert panel.text.count('class="heat-cell') == 168
        assert "Default dream window" in panel.text and "Still learning your hours" in panel.text
        assert 'style="' not in panel.text


def test_dream_hours_are_saved_and_checked(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project) as web:
        web.post(
            "/improve/rhythm/hours",
            {"mode": "fixed", "fixed_start": "2", "fixed_end": "6", "timezone": "UTC"},
        )
        conf = rhythm.load_settings(db)
        saved = (conf.mode, conf.fixed_start, conf.fixed_end, conf.timezone)
        assert saved == ("fixed", 2, 6, "UTC")
        web.post("/improve/rhythm/hours", {"mode": "learn", "timezone": "Nowhere/Land"})
        assert rhythm.load_settings(db).mode == "fixed"  # refused, nothing changed


def test_dream_now_runs_a_dream(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project) as web:
        response = web.post("/improve/dream", {})
        assert response.status_code in (200, 303)
        task = web.app.state.manual_dream
        deadline = time.monotonic() + 30
        while not task.done() and time.monotonic() < deadline:
            web.client.portal.call(asyncio.sleep, 0.1)  # type: ignore[union-attr]
        (dream,) = recent_dreams(db)
        assert dream.trigger == "manual" and dream.status == "succeeded", dream.results
        panel = web.client.get("/improve/rhythm", headers={"HX-Request": "true"}).text
        assert f"/projects/{project.slug}/knowledge" in panel
