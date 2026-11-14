"""Environments through the web UI: a project's own, and a card's preview."""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import git, project_toml
from prat.config import Settings
from prat.db import Database, Project
from prat.domain import CardStatus
from prat.environments import preview_path
from web_support import make_web

pytestmark = pytest.mark.integration


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def environment_toml(port: int) -> str:
    return f"""
[[environment]]
name = "web"
label = "Web app"
start = "{{python}} -m http.server {{port}} --bind 127.0.0.1"
port = {port}
health = "http://127.0.0.1:{{port}}/"
ready_seconds = 20

[[environment.link]]
label = "Home page"
url = "http://127.0.0.1:{{port}}/"
"""


def wait_for(web: object, path: str, text: str) -> str:
    page = ""
    for _ in range(150):
        page = web.client.get(path).text  # type: ignore[attr-defined]
        if text in page:
            return page
        time.sleep(0.1)
    return page


def test_start_and_stop_a_project_environment(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    port = free_port()
    project = project_factory("shop", project_toml=project_toml(extra=environment_toml(port)))
    with make_web(settings, db, project) as web:
        page = web.client.get("/projects/shop").text
        assert 'hx-get="/projects/shop/environments"' in page
        panel = web.client.get("/projects/shop/environments").text
        assert "Web app" in panel and "Stopped" in panel and "/environments/web/start" in panel

        assert web.post("/projects/shop/environments/web/start").status_code == 303
        panel = wait_for(web, "/projects/shop/environments", "Running")
        assert "Running" in panel
        assert f'href="http://127.0.0.1:{port}/"' in panel and "Home page" in panel
        assert "is starting" in web.client.get("/projects/shop").text

        assert web.post("/projects/shop/environments/web/stop").status_code == 303
        assert "Stopped" in web.client.get("/projects/shop/environments").text
        assert web.post("/projects/shop/environments/nope/start").status_code == 404
        assert web.post("/projects/shop/environments/web/explode").status_code == 404


def test_projects_list_starts_environments_and_links_to_them(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    port = free_port()
    project = project_factory("shop", project_toml=project_toml(extra=environment_toml(port)))
    with make_web(settings, db, project) as web:
        listing = web.client.get("/projects").text
        assert 'hx-get="/projects/shop/environments?compact=1"' in listing
        strip = web.client.get("/projects/shop/environments?compact=1").text
        assert "Web app" in strip and "Stopped" in strip
        assert 'name="next" value="/projects"' in strip

        started = web.post("/projects/shop/environments/web/start", {"next": "/projects"})
        assert started.headers["location"] == "/projects"
        strip = wait_for(web, "/projects/shop/environments?compact=1", "Running")
        assert f'href="http://127.0.0.1:{port}/"' in strip and "Restart" in strip
        restarted = web.post("/projects/shop/environments/web/restart", {"next": "//evil"})
        assert restarted.headers["location"] == "/board"
        wait_for(web, "/projects/shop/environments?compact=1", "Running")
        web.post("/projects/shop/environments/web/stop")


def test_preview_a_card_in_review(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    port = free_port()
    project = project_factory("shop", project_toml=project_toml(extra=environment_toml(port)))
    with make_web(settings, db, project) as web:
        card_id = web.add_card(ready=True)
        web.post(f"/cards/{card_id}/run")
        assert web.wait_for(card_id, CardStatus.REVIEW) == CardStatus.REVIEW
        assert 'hx-get="/cards/1/preview"' in web.client.get(f"/cards/{card_id}").text
        panel = web.client.get(f"/cards/{card_id}/preview").text
        assert "Preview" in panel and "Stopped" in panel

        web.post(f"/cards/{card_id}/preview/web/start")
        panel = wait_for(web, f"/cards/{card_id}/preview", "Running")
        assert "Running" in panel
        assert f":{port}/" not in panel  # a preview gets its own port, not the project's
        # The project's own environment is untouched.
        assert "Stopped" in web.client.get("/projects/shop/environments").text
        checkout = preview_path(settings.home, "shop", card_id)
        assert (checkout / "hello.py").is_file()  # the card's own work, from its branch
        web.post(f"/cards/{card_id}/preview/web/stop")
        assert "Stopped" in web.client.get(f"/cards/{card_id}/preview").text
        assert not checkout.exists()


def test_no_environments_explains_how(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project) as web:
        panel = web.client.get(f"/projects/{project.slug}/environments").text
        assert "No environments yet" in panel and "[[environment]]" in panel


def test_environments_and_links_defined_on_this_machine_only(
    settings: Settings, db: Database, project: Project
) -> None:
    """A project whose repository carries no environments can still be run from prat."""
    port = free_port()
    local = settings.home / "environments" / f"{project.slug}.toml"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(
        environment_toml(port)
        + '\n[[link]]\nlabel = "Local site"\nurl = "https://site.localhost.test/"\n',
        encoding="utf-8",
    )
    with make_web(settings, db, project) as web:
        panel = web.client.get(f"/projects/{project.slug}/environments").text
        assert "Web app" in panel and "Stopped" in panel
        assert 'href="https://site.localhost.test/"' in panel and "Local site" in panel
        assert web.post(f"/projects/{project.slug}/environments/web/start").status_code == 303
        panel = wait_for(web, f"/projects/{project.slug}/environments", "Running")
        assert f'href="http://127.0.0.1:{port}/"' in panel
        web.post(f"/projects/{project.slug}/environments/web/stop")

        local.write_text("[[link]]\nlabel = 1\n", encoding="utf-8")
        panel = web.client.get(f"/projects/{project.slug}/environments").text
        assert "was not applied" in panel and "Local site" not in panel


def test_check_environments_files_and_queues_an_infra_card(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    """Two environments that never name each other are a gap: the check files an infra card
    and queues it."""
    second = environment_toml(free_port()).replace('name = "web"', 'name = "docs"')
    project = project_factory(
        "shop", project_toml=project_toml(extra=environment_toml(free_port()) + second)
    )
    checkout = Path(project.checkout_path)
    git(checkout.parent, "clone", "-q", str(checkout), project.agent_path)
    with make_web(settings, db, project) as web:
        panel = web.client.get("/projects/shop/environments").text
        assert "Environment check" in panel and "not run yet" in panel
        assert web.post("/projects/shop/environments/check").status_code == 303
        panel = wait_for(web, "/projects/shop/environments", "Problems found")
        assert "nothing proves they reach each other" in panel
        assert 'href="/cards/1"' in panel and "Infra card #1" in panel
        assert web.wait_for(1, CardStatus.FAILED, CardStatus.REVIEW)  # it was queued and ran
        assert "Infra</span>" in web.client.get("/cards/1").text
