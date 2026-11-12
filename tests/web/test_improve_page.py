"""The self-improvement page: state, controls, findings and the feed."""

from __future__ import annotations

import pytest

from prat.config import Settings
from prat.db import Database, ImproveFinding, Project
from prat.domain import FindingSource, FindingStatus, Severity
from prat.improve.events import EventKind, recent_events
from prat.improve.findings import FindingDraft, record_findings
from prat.improve.settings import ImproveMode, load_improve_settings
from web_support import make_web

pytestmark = pytest.mark.integration


def add_finding(db: Database, title: str = "Gate output is cut too early") -> int:
    with db.session() as session:
        (recorded,) = record_findings(
            session,
            [
                FindingDraft(
                    source=FindingSource.DIAGNOSTIC,
                    fingerprint=title,
                    title=title,
                    problem="Failures past 40 lines lose the cause.",
                    proposal="Keep the last 200 lines.",
                    acceptance_criteria=["the tail keeps 200 lines"],
                    severity=Severity.HIGH,
                )
            ],
        )
        return recorded.finding_id


def test_page_before_setup(settings: Settings, db: Database, project: Project) -> None:
    add_finding(db)
    with make_web(settings, db, project) as web:
        page = web.client.get("/improve").text
        assert "Set up self-improvement" in page
        assert "Turn on" not in page and "Run a cycle now" not in page
        assert "Gate output is cut too early" in page
        assert "Failures past 40 lines lose the cause." in page
        assert "diagnostic finding: Gate output is cut too early" in page
        assert 'aria-current="page"' in page and 'href="/improve"' in page


def test_controls_change_settings_and_feed(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project) as web:
        assert web.post("/improve/toggle", {"enabled": "1"}).status_code == 303
        assert web.post("/improve/mode", {"mode": "review"}).status_code == 303
        saved = web.post(
            "/improve/settings",
            {"interval_minutes": "90", "max_open_cards": "4", "research_enabled": "1"},
        )
        assert saved.status_code == 303
        with db.session() as session:
            conf = load_improve_settings(session)
            kinds = {e.kind for e in recent_events(session)}
        assert conf.enabled and conf.mode == ImproveMode.REVIEW
        assert conf.interval_minutes == 90 and conf.max_open_cards == 4
        assert conf.research_enabled and not conf.push_enabled
        assert EventKind.TOGGLED.value in kinds

        page = web.client.get("/improve").text
        assert "Limits saved." in page
        assert "self-improvement turned on by web" in page

        web.post("/improve/settings", {"interval_minutes": "1"})
        assert "invalid self-improvement setting" in web.client.get("/improve").text
        with db.session() as session:
            assert load_improve_settings(session).interval_minutes == 90

        web.post("/improve/mode", {"mode": "sometimes"})
        with db.session() as session:
            assert load_improve_settings(session).mode == ImproveMode.REVIEW


def test_dismiss_a_finding(settings: Settings, db: Database, project: Project) -> None:
    finding_id = add_finding(db)
    with make_web(settings, db, project) as web:
        assert web.post(f"/improve/findings/{finding_id}/dismiss").status_code == 303
        with db.session() as session:
            assert session.get(ImproveFinding, finding_id).status == FindingStatus.DISMISSED  # type: ignore[union-attr]
        page = web.client.get("/improve").text
        assert f"Finding {finding_id} dismissed." in page
        web.post("/improve/findings/999/dismiss")
        assert "999" in web.client.get("/improve").text


def test_run_now_needs_setup(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project) as web:
        web.post("/improve/toggle", {"enabled": "1"})
        assert web.post("/improve/run").status_code == 303
        page = web.client.get("/improve").text
        assert "Cycle started" in page
        assert "not set up" in page


def test_changes_need_csrf(settings: Settings, db: Database, project: Project) -> None:
    with make_web(settings, db, project) as web:
        refused = web.post("/improve/toggle", {"enabled": "1"}, csrf=False)
        assert refused.status_code == 403
        with db.session() as session:
            assert not load_improve_settings(session).enabled


def test_let_a_held_finding_through(settings: Settings, db: Database, project: Project) -> None:
    finding_id = add_finding(db, "Bounded repair loop")
    with db.session() as session:
        row = session.get(ImproveFinding, finding_id)
        assert row is not None
        row.status = FindingStatus.DISMISSED
        row.decision_note = "screened out: asks to weaken a protection"
    with make_web(settings, db, project) as web:
        page = web.client.get("/improve").text
        assert "Held by the screen" in page and "asks to weaken a protection" in page
        assert f"/improve/findings/{finding_id}/allow" in page
        assert web.post(f"/improve/findings/{finding_id}/allow").status_code == 303
        with db.session() as session:
            again = session.get(ImproveFinding, finding_id)
            assert again is not None and again.status == FindingStatus.NEW
            assert (again.decision_note or "").startswith("let through by web")
        after = web.client.get("/improve").text
        assert "back in the queue" in after and "Held by the screen" not in after
        web.post(f"/improve/findings/{finding_id}/allow")
        assert "was not held back by the screen" in web.client.get("/improve").text


def test_the_overlay_section(settings: Settings, db: Database, project: Project) -> None:
    from conftest import git
    from prat.overlay import overlay_path

    with make_web(settings, db, project) as web:
        page = web.client.get("/improve").text
        assert "Your overlay" in page and "Set up the overlay" in page
        assert web.post("/improve/overlay/init").status_code == 303
        root = overlay_path(settings.home)
        rules = '[[deny_command]]\npattern = "make deploy*"\nreason = "platform team"\n'
        (root / "rules" / "deploys.toml").write_text(rules, encoding="utf-8")
        git(root, "add", "--all")
        git(root, "commit", "--quiet", "-m", "Deny deploys")
        page = web.client.get("/improve").text
        assert "rules/deploys.toml" in page and "make deploy*" in page
        assert "Deny deploys" in page and 'action="/improve/overlay/init"' not in page

        sha = git(root, "rev-parse", "HEAD")
        assert web.post(f"/improve/overlay/undo/{sha}").status_code == 303
        assert not (root / "rules" / "deploys.toml").exists()
        assert f"Overlay change {sha[:10]} undone" in web.client.get("/improve").text
        web.post("/improve/overlay/undo/not-a-commit")
        assert "That is not a commit id." in web.client.get("/improve").text
