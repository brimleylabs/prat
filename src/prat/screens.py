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
}


def report(found: Sequence[Fault], url: str = "") -> str:
    """The faults as text for the builder (or a person), grouped by rule."""
    if not found:
        return f"No layout faults found{' on ' + url if url else ''}."
    widths: dict[tuple[str, str], list[int]] = {}
    for fault in found:
        widths.setdefault((fault.rule, fault.element), []).append(fault.width)
    lines = [f"Layout faults{' on ' + url if url else ''}:"]
    for rule, title in SAID.items():
        mine = [f for f in merged(found) if f.rule == rule]
        if not mine:
            continue
        standard = next((f.standard for f in mine if f.standard), "")
        lines += ["", f"{title}{' (' + standard + ')' if standard else ''}"]
        for fault in mine:
            at = ", ".join(f"{w} px" for w in sorted(set(widths[(fault.rule, fault.element)])))
            lines.append(f"- {fault.element}: {fault.detail} [at {at}]")
    return "\n".join(lines)


def check_url(
    url: str,
    *,
    widths: Sequence[int] = WIDTHS,
    height: int = HEIGHT,
    wait_ms: int = 500,
    timeout_ms: int = 30_000,
) -> list[Fault]:
    """Open ``url`` at each width and return what is wrong on it. Needs Playwright
    (``pip install playwright`` and ``playwright install chromium``)."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found,unused-ignore]
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise RuntimeError(
            "checking a screen needs Playwright: pip install playwright, then "
            "playwright install chromium"
        ) from exc
    found: list[Fault] = []
    with sync_playwright() as play:  # pragma: no cover - needs a browser
        browser = play.chromium.launch()
        try:
            for width in widths:
                page = browser.new_page(viewport={"width": int(width), "height": height})
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                page.wait_for_timeout(wait_ms)
                found += faults(page.evaluate(COLLECT_JS))
                page.close()
        finally:
            browser.close()
    return found
