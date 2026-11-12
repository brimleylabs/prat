"""Adding a project from a repository link on the web: branch picker, clone, setup card."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from conftest import DEFAULT_TOML, git, make_repo
from prat import store
from prat.config import Settings
from prat.db import Card, Database, Project
from prat.domain import CardKind
from web_support import make_web


def test_add_a_project_from_a_link_with_a_branch_and_the_setup_agent(
    settings: Settings, db: Database, project: Project, tmp_path: Path
) -> None:
    origin = make_repo(tmp_path / "remote", "shop", files={"app.py": "print('hi')\n"})
    git(origin, "branch", "develop")
    link = origin.resolve().as_uri()
    settings.projects_dir = tmp_path / "prat-projects"
    with make_web(settings, db, project) as web:
        picker = web.client.get("/project-branches", params={"checkout": link})
        assert picker.status_code == 200
        assert '<option value="main" selected>main (default)</option>' in picker.text
        assert '<option value="develop">' in picker.text
        assert web.client.get("/project-branches", params={"checkout": "/a/path"}).text.strip()

        response = web.post("/projects", {"checkout": link, "branch": "develop", "setup": "on"})
        assert response.status_code == 303, response.text
        assert response.headers["location"].startswith("/cards/")

    with db.session() as session:
        added = store.get_project(session, "shop")
        assert added.default_branch == "develop"
        assert Path(added.checkout_path) == (tmp_path / "prat-projects" / "shop").resolve()
        (card,) = session.query(Card).filter(Card.project_id == added.id).all()
        assert card.kind is CardKind.SETUP and card.title == "Set up shop for PRAT"


def test_a_link_that_cannot_be_read_is_explained(
    settings: Settings, db: Database, project: Project, tmp_path: Path
) -> None:
    settings.projects_dir = tmp_path / "prat-projects"
    with make_web(settings, db, project) as web:
        link = (tmp_path / "nothing-here").resolve().as_uri()
        picker = web.client.get("/project-branches", params={"checkout": link})
        assert "Could not list the branches" in picker.text
        response = web.post("/projects", {"checkout": link})
        assert response.status_code == 422
        assert link in response.text


def test_no_setup_card_when_the_project_already_has_a_project_file(
    settings: Settings,
    db: Database,
    project: Project,
    repo_factory: Callable[..., Path],
) -> None:
    repo = repo_factory("widgets", project_toml=DEFAULT_TOML.replace("acme-api", "widgets"))
    with make_web(settings, db, project) as web:
        response = web.post("/projects", {"checkout": str(repo), "setup": "on"})
        assert response.headers["location"] == "/projects/widgets"
    with db.session() as session:
        added = store.get_project(session, "widgets")
        assert session.query(Card).filter(Card.project_id == added.id).count() == 0


def test_start_the_setup_agent_on_a_registered_project(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    bare = project_factory(name="widgets", project_toml=None)
    with make_web(settings, db, bare) as web:
        page = web.client.get("/projects/widgets")
        assert "Set up with the agent" in page.text
        response = web.post("/projects/widgets/setup")
        assert response.status_code == 303 and response.headers["location"].startswith("/cards/")
    with db.session() as session:
        (card,) = session.query(Card).filter(Card.project_id == bare.id).all()
        assert card.kind is CardKind.SETUP
