"""The ledger (prat.ledger): labels read from a change, and what an exported entry may hold."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from prat import ledger


def files(*items: tuple[str, int]) -> list[dict[str, object]]:
    return [{"path": path, "added": added, "deleted": 0} for path, added in items]


def an_entry(**values: object) -> ledger.Entry:
    base: dict[str, object] = dict(
        run="r1", card="c1", codebase="p1", month="2026-11", work_type="feature",
        area="backend API", language="Python", change_size="small (21 to 100)", lines_changed=60,
        files_changed=2, with_tests=True, interactive_ui=None, codebase_scale=None,
        complexity="medium", spec="described", part_of_epic=False,
        models={"builder": "model-a"}, efforts={}, racing=2, thinking=True, attempt=1,
        fact_pack=None, sent_back_by=None, status="succeeded", seconds=100.0, step_seconds={},
        cost_usd=0.5,
        gates_passed=True, finish_checks=0, verdict="met", landed=True,
    )  # fmt: skip
    return ledger.Entry(**{**base, **values})  # type: ignore[arg-type]


def test_a_path_belongs_to_the_most_specific_area() -> None:
    assert ledger.area_of("apps/web-app/src/components/Reviews.test.tsx") == "tests"
    assert ledger.area_of("apps/web-app/src/components/Reviews.tsx") == "frontend UI"
    assert ledger.area_of("packages/db/migrations/0046_reviews.sql") == "database"
    assert ledger.area_of("apps/api/src/routes/reviews.ts") == "backend API"
    assert ledger.area_of("apps/mobile-app/src/screens/Trip.tsx") == "mobile"
    assert ledger.area_of(".github/workflows/deploy.yml") == "infrastructure"
    assert ledger.area_of("src/prat/ledger.py") == "application code"


def test_a_feature_with_its_tests_is_not_tests() -> None:
    work = ledger.work_of(files(("api/routes/reviews.py", 40), ("tests/test_reviews.py", 200)))
    assert work.area == "backend API" and work.with_tests and work.language == "Python"
    assert work.lines == 240 and work.files == 2
    only_tests = ledger.work_of(files(("tests/test_reviews.py", 200)))
    assert only_tests.area == "tests"


def test_the_type_of_work_is_read_from_the_card() -> None:
    assert ledger.work_type("Fix the crash when a trip has no stops") == "bug fix"
    assert ledger.work_type("Refactor the route planner into services") == "refactor"
    assert ledger.work_type("Campground reviews on the web planner") == "feature"


def test_sizes_are_broad_bands() -> None:
    assert ledger.change_size(12).startswith("tiny")
    assert ledger.change_size(250).startswith("medium")
    assert ledger.change_size(900).startswith("large")
    assert ledger.change_size(5000).startswith("very large")
    assert ledger.codebase_scale(None) is None
    assert (ledger.codebase_scale(150) or "").startswith("small")
    assert (ledger.codebase_scale(9000) or "").startswith("large")


def test_ids_are_stable_here_and_differ_between_installations(tmp_path: Path) -> None:
    one, again, other = (
        ledger.Anonymiser(tmp_path / "a"),
        ledger.Anonymiser(tmp_path / "a"),
        ledger.Anonymiser(tmp_path / "b"),
    )
    assert one("project", 3) == again("project", 3) != other("project", 3)
    assert one("project", 3) != one("card", 3)


def test_an_exported_entry_round_trips_and_names_nothing(tmp_path: Path) -> None:
    path = ledger.export([an_entry()], tmp_path)
    line = json.loads(path.read_text(encoding="utf-8"))
    assert set(line) == set(asdict(an_entry()))
    # Nothing an entry holds can carry a name, a path or a card's words.
    assert not {"title", "goal", "project", "client", "path", "files", "branch"} & set(line)
    assert ledger.load([path, path]) == [an_entry()]


def test_settings_come_most_often_right_first_then_soonest() -> None:
    entries = [
        an_entry(run="1", models={"builder": "fast"}, seconds=50.0, verdict="not_met"),
        an_entry(run="2", models={"builder": "fast"}, seconds=60.0),
        an_entry(run="3", models={"builder": "sure"}, seconds=90.0),
        an_entry(run="4", models={"builder": "sure"}, efforts={"builder": "low"}, seconds=70.0),
        an_entry(run="5", models={"builder": "sure"}, seconds=110.0, sent_back_by="a person"),
    ]
    rows = ledger.table(entries)
    assert [r.setting for r in rows] == ["sure, low effort", "sure", "fast"]
    sure = rows[1]
    assert (sure.runs, sure.right, sure.seconds, sure.sent_back_by_person) == (2, 2, 100.0, 1)
    assert rows[2].right_share == 0.5 and rows[2].seconds == 60.0
