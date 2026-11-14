"""Clients: who each project is for, and pages narrowed to one of them."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import DEFAULT_TOML, add_card, git
from prat import store
from prat.cli import app
from prat.config import Settings
from prat.db import Database, Project
from prat.web.app import ALL_CLIENTS, CLIENT_COOKIE
from web_support import make_web

pytestmark = pytest.mark.integration


def assign(db: Database, project: str, client: str | None) -> None:
    with db.session() as session:
        chosen = store.get_client(session, client) if client else None
        store.set_project_client(session, store.get_project(session, project), chosen)


def test_store_clients(db: Database, project_factory: Callable[..., Project]) -> None:
    project_factory("shop")
    project_factory("blog")
    with db.session() as session:
        acme = store.add_client(session, "Acme Corp")
        store.add_client(session, "Personal")
        assert acme.slug == "acme-corp"
        with pytest.raises(ValueError, match="already exists"):
            store.add_client(session, "acme corp")
        with pytest.raises(ValueError, match="empty"):
            store.add_client(session, "  ")
        store.set_project_client(session, store.get_project(session, "shop"), acme)
    with db.session() as session:
        acme = store.get_client(session, "acme-corp")
        assert [p.slug for p in store.list_projects(session, acme)] == ["shop"]
        assert [p.slug for p in store.list_projects(session)] == ["blog", "shop"]
        assert [c.name for c in store.list_clients(session)] == ["Acme Corp", "Personal"]
        assert store.remove_client(session, "acme-corp") == ("acme-corp", 1)
    with db.session() as session:
        assert store.get_project(session, "shop").client is None


def test_pages_follow_the_active_client(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    shop = project_factory("shop")
    blog = project_factory("blog")
    with db.session() as session:
        store.add_client(session, "Acme")
        store.add_client(session, "Personal")
    assign(db, "shop", "acme")
    assign(db, "blog", "personal")
    add_card(db, shop, title="Shop card")
    add_card(db, blog, title="Blog card")
    with make_web(settings, db, shop, default_client="acme") as web:
        # The installation's default client applies until someone picks another.
        board = web.client.get("/board").text
        assert "Shop card" in board
        assert "Blog card" not in board
        projects = web.client.get("/projects").text
        assert "/projects/shop" in projects
        assert "/projects/blog" not in projects
        assert 'value="acme" selected' in projects

        switched = web.post("/client", {"client": "personal", "next": "/projects"})
        assert switched.status_code == 303
        assert switched.headers["location"] == "/projects"
        assert web.client.cookies.get(CLIENT_COOKIE) == "personal"
        projects = web.client.get("/projects").text
        assert "/projects/blog" in projects
        assert "/projects/shop" not in projects
        new_card = web.client.get("/cards/new").text
        assert 'value="blog"' in new_card
        assert 'value="shop"' not in new_card
        assert "Blog card" in web.client.get("/board/columns").text
        assert "Shop card" not in web.client.get("/board/columns").text

        # All clients is remembered, so the default does not come back.
        web.post("/client", {"client": "", "next": "https://evil.example/"})
        assert web.client.cookies.get(CLIENT_COOKIE) == ALL_CLIENTS
        board = web.client.get("/board").text
        assert "Shop card" in board
        assert "Blog card" in board

        assert web.post("/client", {"client": "nope"}).status_code == 422


def test_set_a_project_client_and_add_a_project_for_one(
    settings: Settings,
    db: Database,
    project: Project,
    repo_factory: Callable[..., Path],
) -> None:
    with db.session() as session:
        store.add_client(session, "Acme")
    with make_web(settings, db, project) as web:
        page = web.client.get(f"/projects/{project.slug}").text
        assert 'action="/projects/acme-api/client"' in page
        assert web.post(f"/projects/{project.slug}/client", {"client": "acme"}).status_code == 303
        with db.session() as session:
            assert store.get_project(session, project.slug).client.slug == "acme"  # type: ignore[union-attr]
        assert web.post(f"/projects/{project.slug}/client", {"client": ""}).status_code == 303
        with db.session() as session:
            assert store.get_project(session, project.slug).client is None

        repo = repo_factory("widgets")
        assert web.post("/projects", {"checkout": str(repo), "client": "acme"}).status_code == 303
        with db.session() as session:
            assert store.get_project(session, "widgets").client.slug == "acme"  # type: ignore[union-attr]


def test_adding_a_project_does_not_hold_the_store_while_its_database_is_made(
    settings: Settings,
    db: Database,
    project: Project,
    repo_factory: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Making a project's database takes seconds on a real server. Running cards write all the
    time, so the store must not be locked meanwhile (SQLite: 'database is locked')."""
    if not db.url.startswith("sqlite"):
        pytest.skip("SQLite only")
    import sqlite3

    from prat.web import routes_actions

    writable: list[bool] = []

    def make_database(_settings: Settings, _slug: str) -> None:
        other = sqlite3.connect(settings.sqlite_path, timeout=0.2)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.rollback()
            writable.append(True)
        except sqlite3.OperationalError:
            writable.append(False)
        finally:
            other.close()

    monkeypatch.setattr(routes_actions, "create_project_database", make_database)
    repo = repo_factory("gadgets", project_toml=None)
    with make_web(settings, db, project) as web:
        response = web.post("/projects", {"checkout": str(repo), "setup": "on"})
        assert response.status_code == 303, response.text
        assert response.headers["location"].startswith("/cards/")
    assert writable == [True]


def test_no_clients_no_switcher(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project) as web:
        assert 'action="/client"' not in web.client.get("/board").text


def test_client_cli(tmp_path: Path, repo_factory: Callable[..., Path]) -> None:
    runner = CliRunner()
    home = tmp_path / "home"

    def cli(*args: str, code: int = 0) -> str:
        result = runner.invoke(app, ["--home", str(home), *args])
        assert result.exit_code == code, result.output
        return result.output

    repo = repo_factory("acme-api", project_toml=DEFAULT_TOML)
    cli("init")
    assert "added client" in cli("client", "add", "HokeTrail")
    cli("client", "add", "Personal")
    cli("client", "add", "HokeTrail", code=1)
    assert "client:     HokeTrail" in cli(
        "project", "add", str(repo), "--client", "hoketrail", "--no-setup"
    )
    assert "acme-api" in cli("client", "list")
    assert "client Personal" in cli("project", "client", "acme-api", "personal")
    assert "client:         Personal" in cli("project", "show", "acme-api")
    assert "no client" in cli("project", "client", "acme-api", "--none")
    cli("project", "client", "acme-api", code=1)
    assert "0 projects now have no client" in cli("client", "remove", "personal", "--yes")


def test_add_a_path_on_another_branch(tmp_path: Path, repo_factory: Callable[..., Path]) -> None:
    runner = CliRunner()
    home = tmp_path / "home"
    repo = repo_factory("widgets", project_toml=None)
    git(repo, "branch", "development")

    def cli(*args: str, code: int = 0) -> str:
        result = runner.invoke(app, ["--home", str(home), *args])
        assert result.exit_code == code, result.output
        return result.output

    cli("init")
    cli("project", "add", str(repo), "--branch", "nope", "--no-setup", code=1)
    assert "branch:     development" in cli(
        "project", "add", str(repo), "--branch", "development", "--no-setup"
    )
