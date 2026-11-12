"""The card page's data changes panel, and undoing a landed card from the web."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from conftest import git
from prat import store
from prat.config import Settings
from prat.db import Database, Project, RunStep
from prat.domain import CardStatus, StepName
from web_support import make_web

pytestmark = pytest.mark.integration


def test_undo_a_landed_card(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project) as web:
        card_id = web.add_card(ready=True)
        web.post(f"/cards/{card_id}/run")
        assert web.wait_for(card_id, CardStatus.REVIEW) == CardStatus.REVIEW
        assert "Undo this card" not in web.client.get(f"/cards/{card_id}").text
        web.post(f"/cards/{card_id}/approve")
        assert web.status(card_id) == CardStatus.DONE
        agent = Path(project.agent_path)
        assert "hello.py" in git(agent, "ls-tree", "--name-only", "main")

        landed = web.client.get(f"/cards/{card_id}").text
        assert "Undo this card" in landed

        assert web.post(f"/cards/{card_id}/undo").status_code == 303
        assert "hello.py" not in git(agent, "ls-tree", "--name-only", "main")
        page = web.client.get(f"/cards/{card_id}").text
        assert "reversed as" in page
        assert "Undone" in page and "Undo this card" not in page
        assert "Undone: code reverted as" in page

        web.post(f"/cards/{card_id}/undo")
        assert "already been undone" in web.client.get(f"/cards/{card_id}").text


def test_data_changes_panel(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project) as web:
        card_id = web.add_card(ready=True)
        web.post(f"/cards/{card_id}/run")
        web.wait_for(card_id, CardStatus.REVIEW)
        assert "Data changes" not in web.client.get(f"/cards/{card_id}").text
        with db.session() as session:
            run = store.latest_run(session, store.get_card(session, card_id))
            assert run is not None
            step = session.scalars(
                select(RunStep).where(RunStep.run_id == run.id, RunStep.name == StepName.VERIFY)
            ).one()
            step.detail = {
                **(step.detail or {}),
                "databases": [
                    {
                        "name": "shop",
                        "database": "shop_db",
                        "branch": f"card-{card_id}",
                        "head": "a1b2c3d4e5f60718",
                        "tables": [
                            {
                                "table": "products",
                                "diff_type": "modified",
                                "data_change": True,
                                "schema_change": False,
                                "rows_added": 2,
                                "rows_deleted": 0,
                                "rows_modified": 5,
                            },
                            {
                                "table": "coupons",
                                "diff_type": "added",
                                "data_change": True,
                                "schema_change": True,
                                "rows_added": 3,
                                "rows_deleted": 0,
                                "rows_modified": 0,
                            },
                        ],
                    }
                ],
            }
        page = web.client.get(f"/cards/{card_id}").text
        assert "Data changes" in page
        assert "shop_db" in page and f"card-{card_id}" in page and "a1b2c3d4e5" in page
        assert "products" in page and "coupons" in page
        assert "+2" in page and ">5<" in page and "schema" in page and "added" in page
