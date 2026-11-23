"""Screens checked without a model: what is broken on a page, from where its elements are.

A builder that adds a control does not see the page. This module opens it at several widths,
measures every visible control and piece of text, and reports what no card asks for and a
person then has to send back: a control sitting under another element, two controls on top of
each other, text cut off, a page wider than its screen, a target too small to press, text too
faint to read. Each fault names the element, the width it was seen at and the numbers, so the
builder that gets the list can fix it without looking.

The rules are published standards where one exists, and say which:

* contrast: WCAG 2.2, 1.4.3 Contrast (Minimum), level AA: 4.5 to 1, or 3 to 1 for large text
  (24 px and up, or 18.66 px and up in bold);
* target size: WCAG 2.2, 2.5.8 Target Size (Minimum), level AA: 24 by 24 CSS pixels; links
  inside a line of text are exempt;
* reflow: WCAG 2.2, 1.4.10 Reflow: no sideways scrolling of the page.

Measuring is one script run in the page (:data:`COLLECT_JS`); judging is plain Python over what
it returns (:func:`faults`), so the rules are tested without a browser. :func:`check_url` drives
a browser through Playwright, which is needed only for that.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

WIDTHS = (390, 768, 1280)
HEIGHT = 900
MAX_FAULTS = 40
# Two controls share more than this much of the smaller one: they are on top of each other.
OVERLAP_SHARE = 0.2
MIN_TARGET = 24.0
CONTRAST = 4.5
CONTRAST_LARGE = 3.0
LARGE_TEXT_PX = 24.0
LARGE_BOLD_PX = 18.66

# What the page is asked for: its size, and a box for every visible control and every element
# with text of its own. Positions are in page coordinates. ``chain`` is the element's ancestors
# (by index), ``pinned`` whether it or an ancestor is fixed or sticky, ``covered_by`` the
# element found at its centre when that is neither it, inside it, nor around it.
COLLECT_JS = r"""
() => {
  const CONTROLS = 'a[href],button,input:not([type=hidden]),select,textarea,summary,' +
    '[role=button],[role=link],[role=tab],[role=checkbox],[role=radio],[role=switch],' +
    '[role=menuitem],[role=option],[onclick],[tabindex]:not([tabindex="-1"])';
  const name = (el) => {
    const parts = [];
    for (let e = el; e && e.nodeType === 1 && parts.length < 4; e = e.parentElement) {
      let s = e.tagName.toLowerCase();
      if (e.id) { parts.unshift(s + '#' + e.id); break; }
      const cls = (e.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean);
      if (cls.length) s += '.' + cls.slice(0, 2).join('.');
      const same = e.parentElement
        ? [...e.parentElement.children].filter((c) => c.tagName === e.tagName) : [];
      if (same.length > 1) s += ':nth-of-type(' + (same.indexOf(e) + 1) + ')';
      parts.unshift(s);
    }
    return parts.join(' > ');
  };
  const solid = (c) => {
    const m = (c || '').match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[,\s\/]+/).filter(Boolean).map(parseFloat);
    return { rgb: p.slice(0, 3), alpha: p.length > 3 ? p[3] : 1 };
  };
  // The colour behind an element, or null when it is an image, a gradient or see-through.
  const behind = (el) => {
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;
      const c = solid(cs.backgroundColor);
      if (c && c.alpha >= 0.99) return c.rgb;
      if (c && c.alpha > 0.01) return null;
    }
    return [255, 255, 255];
  };
  const all = [...document.querySelectorAll('body *')];
  const index = new Map(all.map((el, i) => [el, i]));
  const boxes = [];
  for (const el of all) {
    const cs = getComputedStyle(el);
    const shown = cs.display !== 'none' && cs.visibility !== 'hidden';
    if (!shown || parseFloat(cs.opacity) === 0) continue;
    const control = el.matches(CONTROLS);
    const text = [...el.childNodes].filter((n) => n.nodeType === 3)
      .map((n) => n.textContent.trim()).join(' ').trim();
    if (!control && !text) continue;
    if (control && (el.disabled || el.getAttribute('aria-hidden') === 'true')) continue;
    let pinned = false;
    const chain = [];
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      const p = getComputedStyle(e).position;
      if (p === 'fixed' || p === 'sticky') pinned = true;
      if (e !== el) chain.push(index.get(e));
    }
    let covered_by = null;
    if (control) {
      el.scrollIntoView({ block: 'center', inline: 'nearest' });
      const at = el.getBoundingClientRect();
      const x = at.left + at.width / 2, y = at.top + at.height / 2;
      if (x >= 0 && y >= 0 && x < innerWidth && y < innerHeight) {
        const top = document.elementFromPoint(x, y);
        if (top && top !== el && !el.contains(top) && !top.contains(el)) covered_by = name(top);
      }
    }
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const colour = solid(cs.color);
    boxes.push({
      id: index.get(el), name: name(el), tag: el.tagName.toLowerCase(), control,
      text: (text || el.getAttribute('aria-label') || el.value || '').slice(0, 60),
      x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height,
      chain, pinned, covered_by,
      inline: cs.display === 'inline',
      cut: !!text && (
        (el.scrollWidth > el.clientWidth + 1 && /hidden|clip/.test(cs.overflowX)) ||
        (el.scrollHeight > el.clientHeight + 1 && /hidden|clip/.test(cs.overflowY))),
      font_px: parseFloat(cs.fontSize) || 0,
      bold: (parseInt(cs.fontWeight, 10) || 400) >= 700,
      colour: text && colour && colour.alpha >= 0.99 ? colour.rgb : null,
      behind: text ? behind(el) : null,
      family: text ? (cs.fontFamily.split(',')[0] || '').replace(/["']/g, '').trim() : '',
      radius: parseFloat(cs.borderTopLeftRadius) || 0,
      fill: ((c) => (c && c.alpha >= 0.99 ? c.rgb : null))(solid(cs.backgroundColor)),
    });
  }
  scrollTo(0, 0);
  return {
    width: innerWidth, height: innerHeight,
    page_width: document.documentElement.scrollWidth, boxes,
  };
}
"""


@dataclass(frozen=True)
class Fault:
    # covered, overlap, cut_text, page_overflow, target_size, contrast
    rule: str
    width: int
    element: str
    detail: str
    standard: str = ""


def luminance(rgb: Sequence[float]) -> float:
    """Relative luminance of an sRGB colour (WCAG 2.2)."""

    def channel(value: float) -> float:
        c = max(0.0, min(255.0, value)) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(one: Sequence[float], other: Sequence[float]) -> float:
    lighter, darker = sorted((luminance(one), luminance(other)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _shared(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    """How much of the smaller of two boxes the other one covers, 0 to 1."""
    wide = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    high = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    if wide <= 0 or high <= 0:
        return 0.0
    return float(wide * high / max(min(a["w"] * a["h"], b["w"] * b["h"]), 1.0))


def _label(box: Mapping[str, Any]) -> str:
    text = " ".join(str(box.get("text") or "").split())
    return f"{box['name']} ({text!r})" if text else str(box["name"])


def faults(page: Mapping[str, Any]) -> list[Fault]:
    """What is wrong on one measured page (the value :data:`COLLECT_JS` returns)."""
    width = int(page["width"])
    boxes = list(page.get("boxes") or [])
    found: list[Fault] = []
    if page.get("page_width", width) > width + 1:
        wide = sorted(
            (b for b in boxes if not b.get("pinned") and b["x"] + b["w"] > width + 1),
            key=lambda b: -(b["x"] + b["w"]),
        )
        found.append(
            Fault(
                "page_overflow",
                width,
                _label(wide[0]) if wide else "page",
                f"the page is {int(page['page_width'])} px wide on a {width} px screen and "
                "scrolls sideways",
                "WCAG 2.2 1.4.10 Reflow",
            )
        )
    controls = [b for b in boxes if b.get("control")]
    for box in controls:
        if box.get("covered_by"):
            found.append(Fault("covered", width, _label(box), f"sits under {box['covered_by']}"))
    for i, a in enumerate(controls):
        for b in controls[i + 1 :]:
            if a.get("pinned") or b.get("pinned"):
                continue  # measured at different scroll positions; ``covered`` speaks for them
            if a["id"] in (b.get("chain") or ()) or b["id"] in (a.get("chain") or ()):
                continue
            share = _shared(a, b)
            if share > OVERLAP_SHARE:
                found.append(
                    Fault(
                        "overlap",
                        width,
                        _label(a),
                        f"overlaps {_label(b)} by {round(share * 100)}% of the smaller one",
                    )
                )
    for box in boxes:
        if box.get("cut"):
            found.append(Fault("cut_text", width, _label(box), "its text is cut off"))
    for box in controls:
        if box.get("inline") and box.get("tag") == "a":
            continue
        if box["w"] < MIN_TARGET - 0.5 or box["h"] < MIN_TARGET - 0.5:
            found.append(
                Fault(
                    "target_size",
                    width,
                    _label(box),
                    f"is {round(box['w'])} by {round(box['h'])} px; the minimum is "
                    f"{int(MIN_TARGET)} by {int(MIN_TARGET)}",
                    "WCAG 2.2 2.5.8 Target Size (Minimum)",
                )
            )
    for box in boxes:
        colour, behind = box.get("colour"), box.get("behind")
        if not colour or not behind:
            continue  # over an image or a see-through layer: not something to judge from here
        size = float(box.get("font_px") or 0)
        large = size >= LARGE_TEXT_PX or (bool(box.get("bold")) and size >= LARGE_BOLD_PX)
        needed = CONTRAST_LARGE if large else CONTRAST
        ratio = contrast(colour, behind)
        if ratio < needed - 0.05:
            found.append(
                Fault(
                    "contrast",
                    width,
                    _label(box),
                    f"contrast is {ratio:.1f} to 1; it needs {needed:g} to 1",
                    "WCAG 2.2 1.4.3 Contrast (Minimum)",
                )
            )
    return found


def merged(found: Iterable[Fault]) -> list[Fault]:
    """The same fault on the same element at several widths, said once with all its widths."""
    seen: dict[tuple[str, str, str], list[int]] = {}
    first: dict[tuple[str, str, str], Fault] = {}
    for fault in found:
        key = (fault.rule, fault.element, re.sub(r"\d+", "#", fault.detail))
        seen.setdefault(key, []).append(fault.width)
        first.setdefault(key, fault)
    return [first[key] for key in seen][:MAX_FAULTS]


SAID = {
    "covered": "A control another element sits on top of",
    "overlap": "Controls on top of each other",
    "cut_text": "Text cut off",
    "page_overflow": "Page wider than the screen",
    "target_size": "Target too small to press",
    "contrast": "Text too faint",
    "off_brand": "Values that are not the brand's own",
}


def report(
    found: Sequence[Fault],
    url: str = "",
    *,
    said: Mapping[str, str] | None = None,
    what: str = "Layout faults",
) -> str:
    """The faults as text for the builder (or a person), grouped by rule. ``said`` gives the
    headings of rules this module does not know (prat.pagecheck)."""
    if not found:
        return f"No {what.lower()} found{' on ' + url if url else ''}."
    widths: dict[tuple[str, str], list[int]] = {}
    for fault in found:
        widths.setdefault((fault.rule, fault.element), []).append(fault.width)
    lines = [f"{what}{' on ' + url if url else ''}:"]
    for rule, title in {**SAID, **(said or {})}.items():
        mine = [f for f in merged(found) if f.rule == rule]
        if not mine:
            continue
        standard = next((f.standard for f in mine if f.standard), "")
        lines += ["", f"{title}{' (' + standard + ')' if standard else ''}"]
        for fault in mine:
            # A fault of the document itself (prat.pagecheck) has no width to name.
            seen = sorted(w for w in set(widths[(fault.rule, fault.element)]) if w)
            at = f" [at {', '.join(f'{w} px' for w in seen)}]" if seen else ""
            lines.append(f"- {fault.element}: {fault.detail}{at}")
    return "\n".join(lines)


def key(fault: Fault) -> str:
    """What makes two faults the same fault, whatever the width and the exact numbers."""
    return f"{fault.rule}|{fault.element}|{re.sub(r'[0-9.]+', '#', fault.detail)}"


def fresh(found: Iterable[Fault], known: Iterable[str]) -> list[Fault]:
    """The faults that were not already there (``known`` holds the keys of those that were)."""
    before = set(known)
    return [fault for fault in found if key(fault) not in before]


# Brand values --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Brand:
    """The values a project's screens are made of (its ``brand.toml``). An empty list means
    that kind of value is not checked."""

    colours: tuple[tuple[int, int, int], ...] = ()
    font_sizes: tuple[float, ...] = ()
    fonts: tuple[str, ...] = ()
    radii: tuple[float, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.colours or self.font_sizes or self.fonts or self.radii)


def parse_colour(text: str) -> tuple[int, int, int]:
    value = text.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        raise ValueError(f"colour {text!r}: use #rgb or #rrggbb")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def brand_from(data: Mapping[str, Any]) -> Brand:
    """A brand from the tables of a ``brand.toml``: ``colours`` (or ``colors``), ``font_sizes``
    in pixels, ``fonts`` (family names) and ``radii`` in pixels."""
    colours = data.get("colours") or data.get("colors") or []
    return Brand(
        colours=tuple(parse_colour(str(c)) for c in colours),
        font_sizes=tuple(float(s) for s in data.get("font_sizes") or []),
        fonts=tuple(str(f).strip().lower() for f in data.get("fonts") or []),
        radii=tuple(float(r) for r in data.get("radii") or []),
    )


def _hex(rgb: Sequence[float]) -> str:
    return "#" + "".join(f"{round(v):02x}" for v in rgb[:3])


def brand_faults(page: Mapping[str, Any], brand: Brand) -> list[Fault]:
    """Values on a measured page that are not among the brand's own. Black, white and a square
    corner are always allowed."""
    width = int(page["width"])
    found: list[Fault] = []
    said: set[tuple[str, str]] = set()

    def known_colour(rgb: Sequence[float]) -> bool:
        if all(v <= 2 for v in rgb[:3]) or all(v >= 253 for v in rgb[:3]):
            return True
        return any(
            max(abs(a - b) for a, b in zip(rgb[:3], c, strict=False)) <= 2 for c in brand.colours
        )

    def say(what: str, value: str, box: Mapping[str, Any], allowed: str) -> None:
        if (what, value) in said:
            return  # one element is enough to find the others by
        said.add((what, value))
        found.append(
            Fault("off_brand", width, _label(box), f"{what} {value} is not one of {allowed}")
        )

    for box in page.get("boxes") or []:
        if brand.colours:
            palette = ", ".join(_hex(c) for c in brand.colours)
            for name in ("colour", "fill"):
                rgb = box.get(name)
                if rgb and not known_colour(rgb):
                    what = "text colour" if name == "colour" else "background"
                    say(what, _hex(rgb), box, f"the brand's colours ({palette})")
        if not str(box.get("text") or "").strip():
            continue
        size = float(box.get("font_px") or 0)
        if brand.font_sizes and size and all(abs(size - s) > 0.5 for s in brand.font_sizes):
            sizes = ", ".join(f"{s:g}" for s in brand.font_sizes)
            say("text size", f"{size:g} px", box, f"the brand's sizes ({sizes} px)")
        family = str(box.get("family") or "").lower()
        if brand.fonts and family and family not in brand.fonts:
            say("font", family, box, f"the brand's fonts ({', '.join(brand.fonts)})")
    for box in page.get("boxes") or []:
        radius = float(box.get("radius") or 0)
        # A corner rounded past half the box is a pill or a circle, whatever the number.
        pill = radius >= min(float(box["w"]), float(box["h"])) / 2 - 0.5
        odd = radius > 0.5 and all(abs(radius - r) > 0.5 for r in brand.radii)
        if brand.radii and odd and not pill:
            radii = ", ".join(f"{r:g}" for r in brand.radii)
            say("corner radius", f"{radius:g} px", box, f"the brand's radii ({radii} px)")
    return found


def measure(
    url: str,
    *,
    widths: Sequence[int] = WIDTHS,
    height: int = HEIGHT,
    wait_ms: int = 500,
    timeout_ms: int = 30_000,
    shots: Any = None,
    name: str = "page",
    before: Any = None,
    after: Any = None,
) -> list[dict[str, Any]]:
    """Open ``url`` at each width and return what was measured there, one page per width. With
    ``shots`` (a folder), a full-page picture of each is saved in it as ``<name>-<width>.png``.
    ``before`` is called with the first page before it loads and ``after`` with that page, what
    ``before`` returned and the response, once it has; what ``after`` returns is kept under
    ``"facts"`` (prat.pagecheck reads the document that way, on a load nothing has scrolled).
    Needs Playwright (``pip install playwright`` and ``playwright install chromium``)."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found,unused-ignore]
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise RuntimeError(
            "checking a screen needs Playwright: pip install playwright, then "
            "playwright install chromium"
        ) from exc
    pages: list[dict[str, Any]] = []
    with sync_playwright() as play:  # pragma: no cover - needs a browser
        browser = play.chromium.launch()
        try:
            for at, width in enumerate(widths):
                page = browser.new_page(viewport={"width": int(width), "height": height})
                state = before(page) if before is not None and at == 0 else None
                response = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                page.wait_for_timeout(wait_ms)
                facts = after(page, state, response) if after is not None and at == 0 else None
                pages.append(dict(page.evaluate(COLLECT_JS)))
                if facts is not None:
                    pages[-1]["facts"] = facts
                if shots is not None:
                    shots.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(shots / f"{name}-{int(width)}.png"), full_page=True)
                page.close()
        finally:
            browser.close()
    return pages


def check_url(
    url: str,
    *,
    widths: Sequence[int] = WIDTHS,
    wait_ms: int = 500,
    brand: Brand | None = None,
    shots: Any = None,
    name: str = "page",
) -> list[Fault]:
    """Open ``url`` at each width and return what is wrong on it, and with ``brand`` what is
    not made of the brand's values."""
    found: list[Fault] = []
    for page in measure(url, widths=widths, wait_ms=wait_ms, shots=shots, name=name):
        found += faults(page)
        if brand is not None and not brand.empty:
            found += brand_faults(page, brand)
    return found
