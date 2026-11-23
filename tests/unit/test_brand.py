"""A project's own values checked on its pages (prat.screens), without a browser."""

from __future__ import annotations

from typing import Any

from prat import screens


def box(id: int, name: str, y: float, **more: Any) -> dict[str, Any]:
    return {
        "id": id, "name": name, "tag": "p", "control": False, "text": "", "x": 0, "y": y,
        "w": 200, "h": 40, "chain": [], "pinned": False, "covered_by": None, "inline": False,
        "cut": False, "font_px": 16, "bold": False, "colour": None, "behind": None,
        "family": "", "radius": 0, "fill": None, **more,
    }  # fmt: skip


def page(*boxes: dict[str, Any]) -> dict[str, Any]:
    return {"width": 390, "height": 900, "page_width": 390, "boxes": list(boxes)}


def test_values_outside_the_brand_are_found_once_each() -> None:
    brand = screens.brand_from(
        {
            "colours": ["#1a73e8", "#333"],
            "font_sizes": [14, 16, 24],
            "fonts": ["Inter"],
            "radii": [4, 8],
        }
    )
    assert brand.colours == ((26, 115, 232), (51, 51, 51)) and not brand.empty
    text = {"text": "Hello", "family": "Inter", "behind": [255, 255, 255]}
    found = screens.brand_faults(
        page(
            box(1, "p.ok", 0, **{**text, "colour": [51, 51, 51]}),
            box(2, "p.odd", 30, **{**text, "colour": [200, 30, 30], "font_px": 15}),
            box(3, "p.same", 60, **{**text, "colour": [200, 30, 30], "font_px": 15}),
            box(4, "p.font", 90, **{**text, "colour": [0, 0, 0], "family": "Arial"}),
            box(5, "button.a", 120, control=True, fill=[26, 115, 232], radius=6),
            box(6, "button.pill", 170, control=True, fill=[255, 255, 255], radius=20),
        ),
        brand,
    )
    said = [f.detail.split(" is not")[0] for f in found]
    assert said == ["text colour #c81e1e", "text size 15 px", "font arial", "corner radius 6 px"]
    assert all(f.rule == "off_brand" for f in found) and "p.odd" in found[0].element
    assert "#1a73e8, #333333" in found[0].detail
    assert "Values that are not the brand's own" in screens.report(found)


def test_a_brand_with_no_values_checks_nothing() -> None:
    odd = box(1, "p", 0, text="x", colour=[1, 200, 3], family="Papyrus", font_px=13, radius=7)
    assert screens.Brand().empty
    assert screens.brand_faults(page(odd), screens.Brand()) == []


def test_only_faults_that_were_not_there_before_are_fresh() -> None:
    small = box(1, "button.icon", 10, control=True, tag="button", w=18, h=18)
    new = box(2, "button.new", 60, control=True, tag="button", w=20, h=20)
    before = screens.faults(page(small))
    fresh = screens.fresh(screens.faults(page(small, new)), [screens.key(f) for f in before])
    assert [f.element for f in fresh] == ["button.new"]
