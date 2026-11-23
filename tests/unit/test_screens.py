"""Layout faults judged from measured boxes (prat.screens), without a browser."""

from __future__ import annotations

from typing import Any

from prat import screens


def box(id: int, name: str, x: float, y: float, w: float, h: float, **more: Any) -> dict[str, Any]:
    return {
        "id": id, "name": name, "tag": "button", "control": True, "text": "", "x": x, "y": y,
        "w": w, "h": h, "chain": [], "pinned": False, "covered_by": None, "inline": False,
        "cut": False, "font_px": 16, "bold": False, "colour": None, "behind": None, **more,
    }  # fmt: skip


def page(*boxes: dict[str, Any], width: int = 390, page_width: int | None = None) -> dict[str, Any]:
    return {"width": width, "height": 900, "page_width": page_width or width, "boxes": list(boxes)}


def rules(found: list[screens.Fault]) -> list[str]:
    return [fault.rule for fault in found]


def test_a_clean_page_has_no_faults() -> None:
    assert (
        screens.faults(
            page(box(1, "button#a", 10, 10, 100, 44), box(2, "button#b", 10, 60, 100, 44))
        )
        == []
    )
    assert "No layout faults" in screens.report([])


def test_controls_on_top_of_each_other_and_covered_ones_are_found() -> None:
    found = screens.faults(
        page(
            box(1, "button#save", 10, 10, 100, 44),
            box(2, "button#cancel", 60, 20, 100, 44),  # across the first
            box(3, "a#help", 300, 10, 60, 44, covered_by="div.banner"),
            # A control inside another one (a link in a card that is itself a link) is not one.
            box(4, "a.card", 10, 200, 300, 120),
            box(5, "a.card > button", 20, 210, 80, 44, chain=[4]),
            # Edges that only touch, and pinned elements measured elsewhere, are not either.
            box(6, "button#x", 10, 400, 100, 44),
            box(7, "button#y", 110, 400, 100, 44),
            box(8, "header > button", 10, 400, 100, 44, pinned=True),
        )
    )
    assert rules(found) == ["covered", "overlap"]
    assert "div.banner" in found[0].detail and "button#cancel" in found[1].detail


def test_cut_text_sideways_scrolling_and_small_targets_are_found() -> None:
    found = screens.faults(
        page(
            box(1, "h2.title", 10, 10, 200, 30, control=False, text="Product name", cut=True),
            box(2, "table.wide", 0, 100, 720, 300, control=False, text="x"),
            box(3, "button.icon", 10, 500, 18, 18),
            box(4, "p > a", 10, 600, 40, 17, tag="a", inline=True),  # a link in a sentence
            width=390,
            page_width=720,
        )
    )
    assert rules(found) == ["page_overflow", "cut_text", "target_size"]
    assert "720 px wide on a 390 px screen" in found[0].detail and "table.wide" in found[0].element
    assert "18 by 18" in found[2].detail and "2.5.8" in found[2].standard


def test_contrast_follows_the_published_formula() -> None:
    assert round(screens.contrast((0, 0, 0), (255, 255, 255)), 1) == 21.0
    assert round(screens.contrast((119, 119, 119), (255, 255, 255)), 2) == 4.48  # #777 on white
    grey = {"control": False, "text": "Hint", "colour": [153, 153, 153], "behind": [255, 255, 255]}
    found = screens.faults(
        page(
            box(1, "p.hint", 10, 10, 200, 20, **grey),  # 2.8 to 1
            box(2, "h1", 10, 40, 200, 40, **{**grey, "font_px": 32, "colour": [130, 130, 130]}),
            box(3, "p.dark", 10, 90, 200, 20, **{**grey, "colour": [60, 60, 60]}),
            box(4, "p.photo", 10, 120, 200, 20, **{**grey, "behind": None}),  # over an image
        )
    )
    assert [(f.rule, f.element.split(" ")[0]) for f in found] == [("contrast", "p.hint")]
    assert "2.8 to 1; it needs 4.5 to 1" in found[0].detail


def test_the_report_groups_faults_and_names_each_width_once() -> None:
    small = box(1, "button.icon", 10, 10, 18, 18)
    found = screens.faults(page(small, width=390)) + screens.faults(page(small, width=1280))
    text = screens.report(found, "http://localhost:3000/planner")
    assert text.count("button.icon") == 1
    assert "Target too small to press (WCAG 2.2 2.5.8 Target Size (Minimum))" in text
    assert "[at 390 px, 1280 px]" in text
