"""What a look at a card's pages comes to (prat.engine.screencheck), without a browser."""

from __future__ import annotations

import json
from pathlib import Path

from prat import pagecheck, screens
from prat.engine import screencheck
from prat.projects import ProjectSpec, Screens


def fault(element: str, rule: str = "target_size") -> screens.Fault:
    return screens.Fault(rule, 390, element, "is 18 by 18 px; the minimum is 24 by 24")


def test_a_card_is_interface_work_when_it_touches_interface_files() -> None:
    wanted = Screens()
    assert wanted.touched(["src/app/Planner.tsx"]) and wanted.touched(["web\\styles\\main.css"])
    assert wanted.touched(["resources/views/home.blade.php"])
    assert not wanted.touched(["api/routes.py", "README.md"])
    assert Screens(paths=["web/*"]).touched(["web/a/b.js"])


def test_only_what_the_card_brought_is_handed_back(tmp_path: Path) -> None:
    found = screencheck.Look(
        looked=True, faults={"/": [fault("button.old"), fault("button.new")], "/about": []}
    )
    before = screencheck.Look(looked=True, faults={"/": [fault("button.old")]})
    fresh = screencheck.new_faults(found, screencheck.Known(before.keys()))
    assert {page: [f.element for f in faults] for page, faults in fresh.items()} == {
        "/": ["button.new"]
    }
    # Not compared: everything is reported, and the builder is told so.
    assert sum(len(f) for f in screencheck.new_faults(found, None).values()) == 2
    told = screencheck.feedback(fresh, compared=True)
    assert "were not there before your change" in told and "button.new" in told
    assert "some may be older" in screencheck.feedback(fresh, compared=False)
    assert screencheck.summary(found, fresh, True) == {
        "looked": True, "reason": "", "seconds": 0.0, "pages": 2, "faults": 2, "new": 1,
        "compared": True, "rules": ["target_size"],
    }  # fmt: skip
    shots = tmp_path / "screens"
    shots.mkdir()
    (shots / "home-390.png").write_bytes(b"png")
    note = screencheck.review_note(shots, found, fresh)
    assert "home-390.png" in note and "button.new" in note and "Design checklist" in note
    assert "Accessibility checklist" not in note  # only the sets the project is held to
    screencheck.write_note(tmp_path, note)
    assert screencheck.read_note(tmp_path) == note and screencheck.read_note(shots) == ""


def test_what_was_already_there_is_kept_by_commit(tmp_path: Path) -> None:
    assert screencheck.load_known(tmp_path, "shop", "abc123") is None
    kept = screencheck.Known(["/|a", "/|b"], {"/": {"script_bytes": 1000.0}})
    screencheck.save_known(tmp_path, "shop", "abc123", kept)
    assert screencheck.load_known(tmp_path, "shop", "abc123") == kept
    assert screencheck.load_known(tmp_path, "shop", "def456") is None
    # A look kept before pages' numbers were is still read.
    screencheck.known_path(tmp_path, "shop", "old").write_text(json.dumps(["/|a"]), "utf-8")
    assert screencheck.load_known(tmp_path, "shop", "old") == screencheck.Known(["/|a"])
    assert screencheck.page_name("/planner/trips?x=1") == "planner-trips-x-1"
    assert screencheck.page_name("/") == "home"


def test_a_project_is_held_to_the_sets_that_apply_to_it() -> None:
    def spec(**screens_table: object) -> ProjectSpec:
        return ProjectSpec.model_validate({"name": "Shop", "screens": screens_table})

    assert screencheck.sets_of(spec()) == ("design", "accessibility", "performance")
    assert screencheck.sets_of(spec(website=True)) == pagecheck.SETS
    assert screencheck.sets_of(spec(website=True, checks=["seo"])) == ("seo",)
    assert screencheck.sets_of(ProjectSpec.model_validate({"name": "Shop"})) == ()


def test_a_page_that_got_heavier_or_slower_is_the_cards_fault(tmp_path: Path) -> None:
    was = {"script_bytes": 100_000.0, "total_bytes": 300_000.0, "lcp_ms": 900.0, "tbt_ms": 300.0}
    now = {"script_bytes": 220_000.0, "total_bytes": 420_000.0, "lcp_ms": 3400.0, "tbt_ms": 310.0}
    found = screencheck.Look(looked=True, faults={"/": []}, metrics={"/": now})
    sets = ("design", "performance")
    fresh = screencheck.new_faults(found, screencheck.Known([], {"/": was}), sets)
    assert sorted(f.rule for f in fresh["/"]) == ["perf.heavier", "perf.heavier", "perf.lcp"]
    # The blocking time was already past its limit and is no worse: not this card's.
    # With nothing to compare with, a noisy clock is not handed to a builder at all.
    assert screencheck.new_faults(found, None, sets) == {}
    assert screencheck.new_faults(found, screencheck.Known([], {"/": was}), ("design",)) == {}
    told = screencheck.feedback(fresh, compared=True)
    assert (
        "Heavier than before this card" in told and "JavaScript: 98 KB before, 215 KB now" in told
    )
    assert "[at" not in told  # a fault of the whole page names no width
    note = screencheck.review_note(
        tmp_path, found, fresh, sets=sets, known=screencheck.Known([], {"/": was})
    )
    assert "Largest Contentful Paint: 3.40 s (before: 0.90 s); limit 2.50 s" in note
    assert "Performance checklist" in note and "Search checklist" not in note


def test_a_builder_is_shown_the_rule_it_broke_with_pages_that_do_it_well() -> None:
    broke = screens.Fault("a11y.image_alt", 0, "img.hero", "has no alt attribute", "WCAG 2.2 1.1.1")
    told = screencheck.feedback({"/": [broke, fault("button.new")]}, compared=True)
    assert "Images with no text alternative (WCAG 2.2 1.1.1)" in told
    assert "The rules behind these, with pages that show them done well:" in told
    assert told.count("https://") >= 2
    # Layout faults carry their own numbers and need no page to copy.
    assert "The rules behind these" not in screencheck.feedback({"/": [fault("b")]}, compared=True)
