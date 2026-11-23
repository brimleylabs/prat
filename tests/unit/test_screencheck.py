"""What a look at a card's pages comes to (prat.engine.screencheck), without a browser."""

from __future__ import annotations

from pathlib import Path

from prat import screens
from prat.engine import screencheck
from prat.projects import Screens


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
    fresh = screencheck.new_faults(found, before.keys())
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
    screencheck.write_note(tmp_path, note)
    assert screencheck.read_note(tmp_path) == note and screencheck.read_note(shots) == ""


def test_what_was_already_there_is_kept_by_commit(tmp_path: Path) -> None:
    assert screencheck.load_known(tmp_path, "shop", "abc123") is None
    screencheck.save_known(tmp_path, "shop", "abc123", ["/|a", "/|b"])
    assert screencheck.load_known(tmp_path, "shop", "abc123") == ["/|a", "/|b"]
    assert screencheck.load_known(tmp_path, "shop", "def456") is None
    assert screencheck.page_name("/planner/trips?x=1") == "planner-trips-x-1"
    assert screencheck.page_name("/") == "home"
