"""A page judged as a document: accessibility, performance, search and AI search.

prat.screens measures where a page's elements are. This module measures what the page is: its
title, its language, the names of its controls, what its images and links say, what it tells a
search engine and an AI search product, and how it loaded (the Core Web Vitals a single load can
show, and the causes a builder can remove).

Each fault names a rule of one of the standard sets PRAT ships (prat.standards): ``design``,
``accessibility``, ``performance``, ``seo`` and ``llm``. A project's pages are judged against the
sets that apply to it; search and AI search rules apply only to a public website.

Measuring is one script installed before the page loads (:data:`WATCH_JS`), one run after it
(:data:`FACTS_JS`) and what the browser was sent (status, headers, ``robots.txt``); judging is
plain Python over that (:func:`faults`), so the rules are tested without a browser.

A single load on a developer's machine is a lab measurement. Timings from it (:func:`slow`)
are compared with the published thresholds but are noisy, so the engine hands them to a
builder only when they are also clearly worse than the default branch. What a server in
production decides (compression, caching, the protocol, HTTPS) is judged only on an address
that is not this machine.
"""

# ruff: noqa: E501  (page scripts and the sentences faults are reported in read better whole)
from __future__ import annotations

import itertools
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from prat.screens import Fault

SETS = ("design", "accessibility", "performance", "seo", "llm")
# Sets that only make sense for a site the public and its crawlers read.
WEBSITE_SETS = ("seo", "llm")

# Core Web Vitals and their lab stand-ins: the "good" boundaries (web.dev).
LCP_MS = 2500.0
CLS = 0.1
FCP_MS = 1800.0
TTFB_MS = 800.0
TBT_MS = 200.0
LONG_TASK_MS = 50.0
DOM_NODES = 1400
# A text file smaller than this gains nothing from compression.
COMPRESS_BYTES = 1400
# Server-delivered text this far short of the rendered text means the content needs a script.
RAW_SHARE = 0.5
RAW_MIN_CHARS = 400
# A timing has to be this much worse than the default branch before a builder hears of it.
WORSE_SHARE = 0.25
# Weights that are the same on every load: how much heavier counts as heavier.
HEAVIER_SHARE = 0.10
HEAVIER_FLOOR = {
    "script_bytes": 20_000,
    "style_bytes": 10_000,
    "image_bytes": 50_000,
    "font_bytes": 20_000,
    "total_bytes": 50_000,
    "requests": 3,
    "dom_nodes": 200,
}
MAX_NAMED = 6

# AI agents that fetch a page to answer a person or to index it for answers (not for training),
# as their providers document them. Blocking one of these removes the site from that product.
AI_ANSWER_AGENTS = (
    "OAI-SearchBot",
    "ChatGPT-User",
    "Claude-SearchBot",
    "Claude-User",
    "PerplexityBot",
    "Perplexity-User",
)
# AI agents that collect pages to train models. Allowing or refusing them is the owner's choice;
# the rule is only that robots.txt says which.
AI_TRAINING_AGENTS = ("GPTBot", "ClaudeBot", "Google-Extended", "Applebot-Extended", "CCBot")
# Lighthouse flags a page whose requests add up to more than this (5,000 KiB).
PAGE_BYTES = 5000 * 1024

# Installed before the page's own scripts: what only an observer present from the start sees.
WATCH_JS = r"""
(() => {
  const p = (window.__prat = { lcp: 0, lcp_el: null, lcp_url: '', cls: 0, long: [] });
  const watch = (type, fn) => {
    try { new PerformanceObserver((l) => l.getEntries().forEach(fn)).observe({ type, buffered: true }); }
    catch (e) { /* this browser does not report it */ }
  };
  watch('largest-contentful-paint', (e) => {
    p.lcp = e.startTime; p.lcp_el = e.element || null; p.lcp_url = e.url || '';
  });
  // Cumulative Layout Shift: the worst burst of shifts (gaps under 1 s, at most 5 s long).
  let burst = 0, first = 0, last = 0;
  watch('layout-shift', (e) => {
    if (e.hadRecentInput) return;
    if (burst && e.startTime - last < 1000 && e.startTime - first < 5000) burst += e.value;
    else { burst = e.value; first = e.startTime; }
    last = e.startTime;
    if (burst > p.cls) p.cls = burst;
  });
  watch('longtask', (e) => p.long.push(e.duration));
})();
"""

# Run once the page has settled: what the document says about itself.
FACTS_JS = r"""
() => {
  const p = window.__prat || { lcp: 0, lcp_el: null, lcp_url: '', cls: 0, long: [] };
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
  const all = (sel, root) => [...(root || document).querySelectorAll(sel)];
  const shown = (el) => {
    const cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden' && el.getClientRects().length > 0;
  };
  const hidden = (el) => !!el.closest('[aria-hidden="true"],[hidden],[inert]');
  const byIds = (ids) => (ids || '').split(/\s+/).filter(Boolean)
    .map((id) => (document.getElementById(id) || {}).textContent || '').join(' ').trim();
  // The name assistive technology would announce, near enough to tell "none" from "some".
  const spoken = (el) => {
    const own = (el.getAttribute('aria-label') || '').trim() || byIds(el.getAttribute('aria-labelledby'));
    if (own) return own;
    if (el.matches('input,select,textarea')) {
      if (el.matches('input[type=submit],input[type=button],input[type=reset]') && el.value) return el.value;
      if (el.matches('input[type=image]')) return (el.alt || '').trim() || (el.title || '').trim();
      const wrap = el.closest('label');
      const forId = el.id ? document.querySelector('label[for="' + CSS.escape(el.id) + '"]') : null;
      return ((forId || wrap || {}).textContent || '').trim() || (el.title || '').trim();
    }
    const text = (el.textContent || '').trim();
    if (text) return text;
    const inner = all('img[alt],svg[aria-label],[aria-label]', el)
      .map((i) => i.getAttribute('alt') || i.getAttribute('aria-label') || '').join(' ').trim();
    const svgTitle = all('svg title', el).map((t) => t.textContent).join(' ').trim();
    return inner || svgTitle || (el.title || '').trim();
  };
  const some = (list) => list.slice(0, 8);
  const meta = (sel) => all(sel, document.head || document).map((m) => (m.getAttribute('content') || '').trim());
  const fold = innerHeight;

  // Images
  const imgs = all('img').filter((i) => !hidden(i));
  const sized = (i) => {
    if (i.getAttribute('width') && i.getAttribute('height')) return true;
    const cs = getComputedStyle(i);
    return cs.aspectRatio && cs.aspectRatio !== 'auto';
  };
  const images = {
    total: imgs.length,
    no_alt: some(imgs.filter((i) => !i.hasAttribute('alt') && i.getAttribute('role') !== 'presentation'
      && i.getAttribute('role') !== 'none').map(name)),
    no_size: some(imgs.filter((i) => shown(i) && !sized(i)).map(name)),
    eager_below: some(imgs.filter((i) => shown(i) && i.getAttribute('loading') !== 'lazy'
      && i.getBoundingClientRect().top + scrollY > fold * 2).map(name)),
  };

  // Controls and links
  const buttons = all('button,[role=button],input[type=submit],input[type=button],input[type=image],summary')
    .filter((e) => shown(e) && !hidden(e));
  const links = all('a[href]').filter((e) => shown(e) && !hidden(e));
  const fields = all('input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=image]):not([type=reset]),select,textarea')
    .filter((e) => shown(e) && !hidden(e));
  const PURPOSE = /^(name|given-name|family-name|email|tel|street-address|address-line1|postal-code|cc-number|cc-exp|cc-csc|cc-name)$/;
  const purposeOf = (f) => {
    const t = ((f.getAttribute('name') || '') + ' ' + (f.id || '') + ' ' + (f.type || '')).toLowerCase();
    if (/e-?mail/.test(t)) return 'email';
    if (/\b(tel|phone|mobile)/.test(t)) return 'tel';
    if (/(first|given)[-_ ]?name/.test(t)) return 'given-name';
    if (/(last|family|sur)[-_ ]?name/.test(t)) return 'family-name';
    if (/(zip|postal|postcode)/.test(t)) return 'postal-code';
    if (/card[-_ ]?(number|num)\b/.test(t)) return 'cc-number';
    return '';
  };
  const fakeLinks = all('a:not([href]),[role=link]:not(a),span[onclick],div[onclick]')
    .filter((e) => shown(e) && !hidden(e) && (e.textContent || '').trim());
  const VAGUE = /^(click here|here|read more|more|learn more|link|this|details)$/i;

  // Headings, landmarks, structure
  const heads = all('h1,h2,h3,h4,h5,h6,[role=heading][aria-level]').filter((e) => shown(e) && !hidden(e));
  const level = (h) => parseInt(h.getAttribute('aria-level') || h.tagName.slice(1), 10);
  const ids = {};
  all('[id]').forEach((e) => { ids[e.id] = (ids[e.id] || 0) + 1; });
  const used = new Set();
  all('label[for]').forEach((l) => used.add(l.getAttribute('for')));
  all('[aria-labelledby],[aria-describedby],[aria-controls]').forEach((e) =>
    ['aria-labelledby', 'aria-describedby', 'aria-controls'].forEach((a) =>
      (e.getAttribute(a) || '').split(/\s+/).filter(Boolean).forEach((i) => used.add(i))));
  const ROLES = new Set(('alert alertdialog application article banner blockquote button caption cell checkbox code ' +
    'columnheader combobox complementary contentinfo definition deletion dialog directory document emphasis feed ' +
    'figure form generic grid gridcell group heading img insertion link list listbox listitem log main marquee math ' +
    'menu menubar menuitem menuitemcheckbox menuitemradio meter navigation none note option paragraph presentation ' +
    'progressbar radio radiogroup region row rowgroup rowheader scrollbar search searchbox separator slider ' +
    'spinbutton status strong subscript superscript switch tab table tablist tabpanel term textbox time timer ' +
    'toolbar tooltip tree treegrid treeitem doc-abstract doc-acknowledgments doc-afterword doc-appendix ' +
    'doc-backlink doc-biblioentry doc-bibliography doc-biblioref doc-chapter doc-colophon doc-conclusion doc-cover ' +
    'doc-credit doc-credits doc-dedication doc-endnote doc-endnotes doc-epigraph doc-epilogue doc-errata ' +
    'doc-example doc-footnote doc-foreword doc-glossary doc-glossref doc-index doc-introduction doc-noteref ' +
    'doc-notice doc-pagebreak doc-pagelist doc-part doc-preface doc-prologue doc-pullquote doc-qna doc-subtitle ' +
    'doc-tip doc-toc graphics-document graphics-object graphics-symbol').split(' '));
  const FOCUS = 'a[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),select:not([disabled]),' +
    'textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
  const viewport = meta('meta[name=viewport]')[0] || '';
  const scale = (viewport.match(/maximum-scale\s*=\s*([0-9.]+)/) || [])[1];

  // Structured data
  const types = [];
  let bad = 0;
  const dates = [];
  const walk = (node) => {
    if (Array.isArray(node)) return node.forEach(walk);
    if (!node || typeof node !== 'object') return;
    if (node['@type']) types.push(...[].concat(node['@type']).map(String));
    ['datePublished', 'dateModified'].forEach((k) => { if (node[k]) dates.push(String(node[k])); });
    Object.values(node).forEach(walk);
  };
  all('script[type="application/ld+json"]').forEach((s) => {
    try { walk(JSON.parse(s.textContent)); } catch (e) { bad += 1; }
  });

  // Loading
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const fcp = (performance.getEntriesByType('paint').find((e) => e.name === 'first-contentful-paint') || {}).startTime || 0;
  const res = performance.getEntriesByType('resource');
  const kind = (r) => {
    const path = r.name.split(/[?#]/)[0].toLowerCase();
    if (r.initiatorType === 'script' || /\.m?js$/.test(path)) return 'script';
    if (/\.css$/.test(path) || (r.initiatorType === 'link' && !/\.(woff2?|ttf|otf|png|jpe?g|gif|svg|webp|avif|ico)$/.test(path))) return 'style';
    if (/\.(woff2?|ttf|otf|eot)$/.test(path)) return 'font';
    if (r.initiatorType === 'img' || /\.(png|jpe?g|gif|svg|webp|avif|ico)$/.test(path)) return 'image';
    return 'other';
  };
  const bytes = { script: 0, style: 0, image: 0, font: 0, other: 0 };
  res.forEach((r) => { bytes[kind(r)] += r.encodedBodySize || 0; });
  const blockingScripts = all('head script[src]').filter((s) => !s.async && !s.defer && s.type !== 'module')
    .map((s) => s.src);
  const faces = [];
  let swapless = 0;
  for (const sheet of document.styleSheets) {
    let rules = [];
    try { rules = [...sheet.cssRules]; } catch (e) { continue; }
    for (const r of rules) {
      if (r.constructor.name !== 'CSSFontFaceRule') continue;
      faces.push(r.style.getPropertyValue('font-family'));
      const d = r.style.getPropertyValue('font-display');
      if (!d || d === 'auto' || d === 'block') swapless += 1;
    }
  }
  const lcpEl = p.lcp_el && p.lcp_el.isConnected ? p.lcp_el : null;
  const heavyOld = res.filter((r) => kind(r) === 'image' && /\.(png|jpe?g)$/.test(r.name.split(/[?#]/)[0].toLowerCase())
    && (r.encodedBodySize || 0) > 100000).map((r) => r.name);

  return {
    url: location.href,
    title: (document.title || '').trim(),
    title_count: all('head title').length,
    lang: (document.documentElement.getAttribute('lang') || '').trim(),
    description: meta('meta[name=description]'),
    canonical: all('link[rel~=canonical]').map((l) => l.getAttribute('href') || ''),
    robots: meta('meta[name=robots],meta[name=googlebot]').join(',').toLowerCase(),
    viewport,
    zoom_blocked: /user-scalable\s*=\s*(no|0)/.test(viewport) || (!!scale && parseFloat(scale) < 2),
    icon: all('link[rel~=icon]').length > 0,
    og: {
      title: meta('meta[property="og:title"]')[0] || '',
      description: meta('meta[property="og:description"]')[0] || '',
      image: meta('meta[property="og:image"]')[0] || '',
      type: meta('meta[property="og:type"]')[0] || '',
      url: meta('meta[property="og:url"]')[0] || '',
    },
    hreflang: all('link[rel~=alternate][hreflang]').map((l) => l.getAttribute('hreflang')),
    h1: heads.filter((h) => level(h) === 1).map((h) => (h.textContent || '').trim().slice(0, 80)),
    headings: heads.map(level),
    main: all('main,[role=main]').filter(shown).length,
    images,
    buttons_unnamed: some(buttons.filter((b) => !spoken(b)).map(name)),
    links_unnamed: some(links.filter((l) => !spoken(l)).map(name)),
    links_vague: some(links.filter((l) => VAGUE.test(spoken(l))).map((l) => name(l) + ' ("' + spoken(l) + '")')),
    fields_unlabelled: some(fields.filter((f) => !spoken(f)).map(name)),
    fields_no_purpose: some(fields.filter((f) => purposeOf(f) && !PURPOSE.test((f.getAttribute('autocomplete') || '').trim().split(/\s+/).pop() || ''))
      .map((f) => name(f) + ' (' + purposeOf(f) + ')')),
    fake_links: some(fakeLinks.map(name)),
    frames_untitled: some(all('iframe').filter((f) => shown(f) && !hidden(f) && !(f.title || '').trim()
      && !(f.getAttribute('aria-label') || '').trim()).map(name)),
    tables_headless: some(all('table').filter((t) => shown(t) && t.getAttribute('role') !== 'presentation'
      && t.getAttribute('role') !== 'none' && t.rows.length > 1 && !t.querySelector('th,[role=columnheader],[role=rowheader]')).map(name)),
    tabindex_positive: some(all('[tabindex]').filter((e) => parseInt(e.getAttribute('tabindex'), 10) > 0).map(name)),
    hidden_focusable: some(all('[aria-hidden="true"]').flatMap((h) => [h, ...all('*', h)])
      .filter((e) => e.matches(FOCUS) && shown(e)).map(name)),
    roles_invalid: some(all('[role]').filter((e) => {
      const r = (e.getAttribute('role') || '').trim().toLowerCase().split(/\s+/);
      return r[0] && !r.some((x) => ROLES.has(x));
    }).map((e) => name(e) + ' (role="' + e.getAttribute('role') + '")')),
    duplicate_ids: some(Object.keys(ids).filter((i) => ids[i] > 1 && used.has(i))),
    autoplay: some(all('video[autoplay],audio[autoplay]').filter((m) => !m.muted && !m.hasAttribute('muted')).map(name)),
    jsonld: { blocks: all('script[type="application/ld+json"]').length, bad, types: [...new Set(types)], dates },
    time_tags: all('time[datetime]').length,
    text_chars: (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').trim().length,
    dom_nodes: all('*').length,
    perf: {
      lcp_ms: Math.round(p.lcp), cls: Math.round(p.cls * 1000) / 1000,
      fcp_ms: Math.round(fcp), ttfb_ms: Math.round(nav.responseStart || 0),
      tbt_ms: Math.round(p.long.reduce((s, d) => s + Math.max(0, d - 50), 0)),
      long_tasks: p.long.length,
      lcp_element: lcpEl ? name(lcpEl) : '',
      lcp_is_image: !!lcpEl && lcpEl.tagName === 'IMG',
      lcp_lazy: !!lcpEl && lcpEl.getAttribute('loading') === 'lazy',
      lcp_priority: !!lcpEl && (lcpEl.getAttribute('fetchpriority') || '') === 'high',
      requests: res.length + 1,
      script_bytes: bytes.script, style_bytes: bytes.style, image_bytes: bytes.image,
      font_bytes: bytes.font,
      total_bytes: (nav.encodedBodySize || 0) + Object.values(bytes).reduce((a, b) => a + b, 0),
      blocking_scripts: some(blockingScripts),
      font_faces: faces.length, font_faces_blocking: swapless,
      heavy_old_images: some(heavyOld),
      protocols: [...new Set(res.map((r) => r.nextHopProtocol).filter(Boolean).concat(nav.nextHopProtocol || []))],
    },
  };
}
"""


@dataclass(frozen=True)
class Check:
    """One thing this module can decide, and the published rule it stands for."""

    set: str
    rule_id: str  # the rule's id in that set's standards file
    said: str  # the heading its faults are listed under
    standard: str = ""
    # Decided by the server a site is published from, not by the code: skipped on this machine.
    server: bool = False
    # Catches some breaches of its rule and not all: the reviewer is still asked about it.
    partial: bool = False
    # The same rule as other sets word it: (set, rule id).
    also: tuple[tuple[str, str], ...] = ()

    @property
    def sets(self) -> tuple[str, ...]:
        return (self.set, *(name for name, _ in self.also))


def _c(
    where: str,
    said: str,
    standard: str = "",
    *also: str,
    server: bool = False,
    partial: bool = False,
) -> Check:
    name, rule_id = where.split("/")
    same = tuple((a.split("/")[0], a.split("/")[1]) for a in also)
    return Check(name, rule_id, said, standard, server, partial, same)


# fmt: off
CHECKS: dict[str, Check] = {
    # Accessibility
    "a11y.image_alt": _c("accessibility/image-alt-present", "Images with no text alternative", "WCAG 2.2 1.1.1 Non-text Content", "seo/image-alt-text"),
    "a11y.control_name": _c("accessibility/control-accessible-name", "Buttons and links with no name", "WCAG 2.2 4.1.2 Name, Role, Value"),
    "a11y.page_lang": _c("accessibility/page-language", "Page with no language", "WCAG 2.2 3.1.1 Language of Page", "seo/html-lang"),
    "a11y.page_title": _c("accessibility/page-title", "Page with no title", "WCAG 2.2 2.4.2 Page Titled", "seo/title-element"),
    "a11y.main_landmark": _c("accessibility/landmarks", "Main content not marked", "HTML Standard, the main element", "llm/main-landmark"),
    "a11y.frame_title": _c("accessibility/frame-title", "Frames with no title", "WCAG 2.2 4.1.2 Name, Role, Value"),
    "a11y.table_headers": _c("accessibility/data-table-headers", "Tables with no header cells", "WCAG 2.2 1.3.1 Info and Relationships"),
    "a11y.tabindex": _c("accessibility/focus-order", "Tab order forced with tabindex", "WCAG 2.2 2.4.3 Focus Order", partial=True),
    "a11y.hidden_focusable": _c("accessibility/aria-hidden-focus", "Hidden from assistive technology but focusable", "WCAG 2.2 4.1.2 Name, Role, Value"),
    "a11y.role": _c("accessibility/aria-valid", "Roles that do not exist", "WAI-ARIA 1.2", partial=True),
    "a11y.duplicate_id": _c("accessibility/duplicate-ids", "An id used twice and referred to", "HTML Standard, the id attribute"),
    "a11y.zoom": _c("accessibility/zoom-not-blocked", "Zoom switched off", "WCAG 2.2 1.4.4 Resize Text"),
    "a11y.autoplay": _c("accessibility/audio-autoplay", "Sound that starts by itself", "WCAG 2.2 1.4.2 Audio Control"),
    # Design rules decided from the document rather than from where things are
    "design.heading_order": _c("design/heading-order", "Heading levels skipped", "W3C WAI Tutorials, Headings", "llm/heading-outline"),
    "design.field_label": _c("design/visible-labels", "Fields with no label", "WCAG 2.2 3.3.2 Labels or Instructions", "accessibility/form-control-name"),
    "design.field_purpose": _c("design/input-purpose", "Fields that do not say what they hold", "WCAG 2.2 1.3.5 Identify Input Purpose"),
    # Performance
    "perf.cls": _c("performance/cls", "Layout that shifts while loading", "Core Web Vitals: CLS 0.1 or less"),
    "perf.image_size": _c("performance/image-dimensions", "Images with no reserved space", "web.dev, Optimize Cumulative Layout Shift"),
    "perf.lcp_lazy": _c("performance/lcp-not-lazy", "The largest image is lazy-loaded", "web.dev, Optimize Largest Contentful Paint"),
    "perf.lcp_priority": _c("performance/lcp-fetchpriority", "The largest image does not say it comes first", "web.dev, Optimize Largest Contentful Paint"),
    "perf.lazy_offscreen": _c("performance/lazy-offscreen", "Images far down the page loaded at once", "web.dev, Browser-level image lazy loading"),
    "perf.blocking_script": _c("performance/render-blocking", "Scripts that block the first paint", "Lighthouse, Eliminate render-blocking resources", partial=True),
    "perf.font_display": _c("performance/font-display", "Fonts that hide text while they load", "Lighthouse, Ensure text remains visible during webfont load"),
    "perf.dom_size": _c("performance/dom-size", "Too many elements", "Lighthouse, Avoid an excessive DOM size"),
    "perf.image_format": _c("performance/image-formats", "Large images in an old format", "Lighthouse, Serve images in modern formats", partial=True),
    "perf.compression": _c("performance/text-compression", "Text sent uncompressed", "Lighthouse, Enable text compression", server=True),
    "perf.cache": _c("performance/cache-static-assets", "Static files with no cache lifetime", "Lighthouse, Serve static assets with an efficient cache policy", server=True),
    "perf.protocol": _c("performance/http2", "Served over HTTP/1.1", "Lighthouse, Use HTTP/2", server=True),
    "perf.weight": _c("performance/page-weight", "Page too heavy", "Lighthouse, Avoid enormous network payloads"),
    "perf.heavier": _c("performance/page-weight", "Heavier than before this card"),
    "perf.lcp": _c("performance/lcp", "Slow to show its main content", "Core Web Vitals: LCP 2.5 s or less"),
    "perf.tbt": _c("performance/tbt", "Main thread blocked while loading", "Lighthouse: Total Blocking Time under 200 ms", "performance/long-tasks"),
    "perf.fcp": _c("performance/fcp", "Slow to show anything", "web.dev: First Contentful Paint 1.8 s or less"),
    "perf.ttfb": _c("performance/ttfb", "Slow to answer", "web.dev: Time to First Byte 0.8 s or less"),
    # Search
    "seo.title": _c("seo/title-element", "Title missing or repeated", "HTML Standard, the title element"),
    "seo.description": _c("seo/meta-description", "No meta description", "Google Search Central, Snippets"),
    "seo.h1": _c("seo/single-h1", "No single main heading", "Google Search Central, SEO Starter Guide"),
    "seo.canonical": _c("seo/canonical-link", "Canonical link wrong", "Google Search Central, Canonicalization", "llm/canonical-url"),
    "seo.noindex": _c("seo/no-accidental-noindex", "Page tells search engines to stay away", "Google Search Central, Block indexing with noindex", "llm/indexable-with-snippet"),
    "seo.viewport": _c("seo/viewport-meta", "No mobile viewport", "Google Search Central, Mobile-first indexing"),
    "seo.links": _c("seo/crawlable-links", "Links a crawler cannot follow", "Google Search Central, Make your links crawlable"),
    "seo.link_text": _c("seo/anchor-text", "Links that do not say where they go", "Google Search Central, Write good anchor text", partial=True),
    "seo.jsonld": _c("seo/structured-data-json-ld", "Structured data that does not parse", "Google Search Central, Structured data guidelines"),
    "seo.status": _c("seo/http-status-codes", "Page does not answer 200", "RFC 9110 HTTP Semantics", partial=True),
    "seo.robots_txt": _c("seo/robots-txt-valid", "robots.txt missing or shutting everyone out", "RFC 9309 Robots Exclusion Protocol", "llm/robots-txt-served-correctly"),
    "seo.sitemap": _c("seo/xml-sitemap", "No sitemap named", "sitemaps.org protocol", partial=True),
    "seo.hreflang": _c("seo/hreflang", "Language alternates that are not valid", "Google Search Central, Localized versions", partial=True),
    "seo.social": _c("seo/open-graph-tags", "No preview for shared links", "The Open Graph protocol"),
    "seo.icon": _c("seo/favicon", "No favicon", "Google Search Central, Favicon"),
    "seo.https": _c("seo/https", "Not served over HTTPS", "Google Search Central, HTTPS", server=True),
    # AI search
    "llm.raw_content": _c("llm/content-in-server-html", "Content that only exists after a script runs", "Google Search Central, JavaScript SEO basics"),
    "llm.agents": _c("llm/search-and-user-agents-allowed", "AI answer agents shut out in robots.txt", "The providers' crawler documentation"),
    "llm.training_agents": _c("llm/training-agents-declared", "No stated choice for AI training crawlers", "The providers' crawler documentation"),
    "llm.snippet": _c("llm/snippet-controls-deliberate", "Page forbids quoting it", "Google Search Central, AI features and your website"),
    "llm.dates": _c("llm/dates-visible-and-machine-readable", "Article with no date a machine can read", "Google Search Central, Byline dates", partial=True),
}
# fmt: on
SAID = {rule: check.said for rule, check in CHECKS.items()}
# The rules PRAT decides by itself, by set: what a builder is told is measured.
MEASURED: dict[str, frozenset[str]] = {
    name: frozenset(
        rule_id
        for c in CHECKS.values()
        if not c.partial
        for set_name, rule_id in ((c.set, c.rule_id), *c.also)
        if set_name == name
    )
    for name in SETS
}


def is_local(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")


def sets_for(*, website: bool, chosen: Sequence[str] | None = None) -> tuple[str, ...]:
    """The standard sets a project's pages are held to."""
    if chosen is not None:
        return tuple(name for name in SETS if name in chosen)
    return tuple(name for name in SETS if website or name not in WEBSITE_SETS)


def _names(items: Iterable[Any]) -> list[str]:
    return [str(item) for item in items][:MAX_NAMED]


def robots_groups(text: str) -> dict[str, list[str]]:
    """``robots.txt`` as agent (lower case) -> its Disallow paths, with ``sitemap`` lines under
    the key ``"sitemap"``. Good enough to see who is shut out of the whole site (RFC 9309)."""
    groups: dict[str, list[str]] = {"sitemap": []}
    agents: list[str] = []
    fresh = True
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        field, value = (part.strip() for part in line.split(":", 1))
        field = field.lower()
        if field == "user-agent":
            if not fresh:
                agents = []
            agents.append(value.lower())
            groups.setdefault(value.lower(), [])
            fresh = True
        elif field == "disallow":
            fresh = False
            for agent in agents:
                groups[agent].append(value)
        elif field == "allow":
            fresh = False
            if value == "/":
                for agent in agents:
                    groups[agent].append("allow:/")
        elif field == "sitemap":
            groups["sitemap"].append(value)
    return groups


def shut_out(groups: Mapping[str, list[str]], agent: str) -> bool:
    """Whether ``agent`` may fetch nothing: its own group, or failing that ``*``, disallows /."""
    rules = groups.get(agent.lower())
    if rules is None:
        rules = groups.get("*", [])
    return "/" in rules and "allow:/" not in rules


def faults(
    facts: Mapping[str, Any],
    sets: Sequence[str] = SETS,
    *,
    width: int = 0,
    local: bool | None = None,
) -> list[Fault]:
    """What is wrong with one page (the value :data:`FACTS_JS` returns, with what the browser
    was sent under ``"fetched"``), for the standard sets asked for. Timings are not judged here
    (:func:`slow`)."""
    url = str(facts.get("url") or "")
    local = is_local(url) if local is None else local
    fetched: Mapping[str, Any] = facts.get("fetched") or {}
    perf: Mapping[str, Any] = facts.get("perf") or {}
    images: Mapping[str, Any] = facts.get("images") or {}
    found: list[Fault] = []

    def say(rule: str, element: str, detail: str) -> None:
        check = CHECKS[rule]
        if any(name in sets for name in check.sets) and not (check.server and local):
            found.append(Fault(rule, width, element, detail, check.standard))

    def each(rule: str, items: Any, detail: str) -> None:
        for item in _names(items or ()):
            say(rule, item, detail)

    # Accessibility
    each("a11y.image_alt", images.get("no_alt"), 'has no alt attribute (use alt="" for decoration)')
    each(
        "a11y.control_name",
        facts.get("buttons_unnamed"),
        "is a button with no text or label to announce",
    )
    each(
        "a11y.control_name",
        facts.get("links_unnamed"),
        "is a link with no text or label to announce",
    )
    if not facts.get("lang"):
        say("a11y.page_lang", "html", "has no lang attribute")
    if not facts.get("title"):
        say("a11y.page_title", "head", "has no title")
    mains = int(facts.get("main") or 0)
    if mains != 1:
        say(
            "a11y.main_landmark",
            "body",
            "has no main element"
            if mains == 0
            else f"has {mains} main elements showing; one is expected",
        )
    each("a11y.frame_title", facts.get("frames_untitled"), "is a frame with no title")
    each("a11y.table_headers", facts.get("tables_headless"), "has rows of data and no header cells")
    each(
        "a11y.tabindex",
        facts.get("tabindex_positive"),
        "has a tabindex above 0, which takes it out of the page's order",
    )
    each(
        "a11y.hidden_focusable",
        facts.get("hidden_focusable"),
        "can be focused but is hidden with aria-hidden",
    )
    each("a11y.role", facts.get("roles_invalid"), "has a role that is not in WAI-ARIA")
    each(
        "a11y.duplicate_id",
        facts.get("duplicate_ids"),
        "is an id on more than one element, and a label or ARIA attribute points at it",
    )
    if facts.get("zoom_blocked"):
        say("a11y.zoom", "meta[name=viewport]", f"stops people zooming ({facts.get('viewport')})")
    each("a11y.autoplay", facts.get("autoplay"), "plays sound without being asked")

    # Design rules read from the document
    levels = [int(level) for level in facts.get("headings") or []]
    for before, after in itertools.pairwise(levels):
        if after > before + 1:
            say("design.heading_order", f"h{after}", f"follows an h{before}; a level was skipped")
            break
    each("design.field_label", facts.get("fields_unlabelled"), "is a field with no label")
    each(
        "design.field_purpose",
        facts.get("fields_no_purpose"),
        "holds personal data and has no matching autocomplete value",
    )

    # Performance: causes, and what one load always shows the same
    shift = float(perf.get("cls") or 0)
    if shift > CLS:
        say("perf.cls", "page", f"shifts by {shift:.2f} while loading; the limit is {CLS:g}")
    each(
        "perf.image_size",
        images.get("no_size"),
        "has no width and height (or aspect-ratio), so the page moves when it arrives",
    )
    if perf.get("lcp_lazy"):
        say(
            "perf.lcp_lazy",
            str(perf.get("lcp_element") or "img"),
            'is the largest thing on the first screen and has loading="lazy"',
        )
    elif perf.get("lcp_is_image") and not perf.get("lcp_priority"):
        say(
            "perf.lcp_priority",
            str(perf.get("lcp_element") or "img"),
            'is the largest thing on the first screen; give it fetchpriority="high"',
        )
    each(
        "perf.lazy_offscreen",
        images.get("eager_below"),
        'is more than two screens down and has no loading="lazy"',
    )
    weight = float(perf.get("total_bytes") or 0)
    if weight > PAGE_BYTES:
        say(
            "perf.weight",
            "page",
            f"weighs {weight / 1024:.0f} KiB; more than {PAGE_BYTES // 1024} KiB is flagged",
        )
    each(
        "perf.blocking_script",
        perf.get("blocking_scripts"),
        "is loaded in the head without async, defer or type=module",
    )
    blocking = int(perf.get("font_faces_blocking") or 0)
    if blocking:
        say(
            "perf.font_display",
            "@font-face",
            f"{blocking} of {int(perf.get('font_faces') or 0)} font faces set no font-display (use swap or optional)",
        )
    nodes = int(facts.get("dom_nodes") or 0)
    if nodes > DOM_NODES:
        say("perf.dom_size", "page", f"has {nodes} elements; more than {DOM_NODES} is flagged")
    each(
        "perf.image_format",
        perf.get("heavy_old_images"),
        "is over 100 KB as PNG or JPEG; AVIF or WebP would be smaller",
    )
    for item in _names(fetched.get("uncompressed") or ()):
        say("perf.compression", item, "is text sent with no gzip or Brotli encoding")
    for item in _names(fetched.get("uncached") or ()):
        say("perf.cache", item, "is a static file with no Cache-Control lifetime")
    protocols = [str(p).lower() for p in perf.get("protocols") or []]
    if protocols and all(p.startswith("http/1") for p in protocols):
        say("perf.protocol", "page", "everything arrived over HTTP/1.1")

    # Search
    # A missing title, alt text or language is said once, under accessibility.
    if facts.get("title") and int(facts.get("title_count") or 1) > 1:
        say("seo.title", "head", f"has {facts.get('title_count')} title elements")
    described = [d for d in facts.get("description") or [] if d]
    if not described:
        say("seo.description", "head", "has no meta description")
    mains_said = len(facts.get("h1") or [])
    if mains_said == 0:
        say("seo.h1", "page", "has no h1 saying what the page is about")
    elif mains_said > 1:
        say("seo.h1", "page", f"has {mains_said} h1 headings; one should name the page")
    canonical = [str(c) for c in facts.get("canonical") or []]
    if len(canonical) > 1:
        say(
            "seo.canonical",
            "link[rel=canonical]",
            f"appears {len(canonical)} times; search engines may ignore all of them",
        )
    elif canonical and not re.match(r"https?://", canonical[0]):
        say("seo.canonical", "link[rel=canonical]", f"is {canonical[0]!r}; use the full address")
    directives = f"{facts.get('robots') or ''},{str(fetched.get('x_robots') or '').lower()}"
    if "noindex" in directives or re.search(r"(^|[,\s])none([,\s]|$)", directives):
        say("seo.noindex", "page", "is marked noindex, so it will be dropped from search results")
    if not facts.get("viewport"):
        say("seo.viewport", "head", "has no viewport meta, so phones show the desktop layout")
    each(
        "seo.links", facts.get("fake_links"), "acts as a link but is not an a element with an href"
    )
    each("seo.link_text", facts.get("links_vague"), "does not say where it goes")
    structured: Mapping[str, Any] = facts.get("jsonld") or {}
    if int(structured.get("bad") or 0):
        say(
            "seo.jsonld",
            "script[type=application/ld+json]",
            f"{structured.get('bad')} block(s) are not valid JSON",
        )
    status = fetched.get("status")
    if status is not None and int(status) != 200:
        say("seo.status", "page", f"answered {status}")
    robots = fetched.get("robots_txt")
    groups = robots_groups(str(robots)) if isinstance(robots, str) else None
    if "robots_txt" in fetched:
        if groups is None:
            say("seo.robots_txt", "/robots.txt", "is missing")
        elif shut_out(groups, "*"):
            say("seo.robots_txt", "/robots.txt", "disallows / for every crawler")
        elif not groups["sitemap"]:
            say("seo.sitemap", "/robots.txt", "names no Sitemap")
    for code in facts.get("hreflang") or []:
        if not re.fullmatch(
            r"x-default|[a-zA-Z]{2,3}(-[a-zA-Z]{4})?(-([a-zA-Z]{2}|\d{3}))?", str(code)
        ):
            say("seo.hreflang", "link[hreflang]", f"{code!r} is not a language code")
    social: Mapping[str, Any] = facts.get("og") or {}
    missing = [name for name in ("title", "type", "image", "url") if not social.get(name)]
    if missing:
        say("seo.social", "head", "has no " + ", ".join(f"og:{name}" for name in missing))
    if "icon" in facts and not facts.get("icon"):
        say("seo.icon", "head", "names no favicon")
    if url.startswith("http://"):
        say("seo.https", "page", "is served over plain HTTP")

    # AI search
    raw = fetched.get("raw_chars")
    shown = int(facts.get("text_chars") or 0)
    if raw is not None and shown >= RAW_MIN_CHARS and int(raw) < shown * RAW_SHARE:
        say(
            "llm.raw_content",
            "page",
            f"the HTML the server sends holds {int(raw)} characters of text and the finished page {shown}; "
            "a crawler that runs no scripts reads only the first",
        )
    if groups is not None and not shut_out(groups, "*"):
        closed = [agent for agent in AI_ANSWER_AGENTS if shut_out(groups, agent)]
        if closed:
            say(
                "llm.agents",
                "/robots.txt",
                "disallows / for " + ", ".join(closed) + ", which fetch pages to answer people",
            )
    if groups is not None:
        unsaid = [agent for agent in AI_TRAINING_AGENTS if agent.lower() not in groups]
        if unsaid:
            say(
                "llm.training_agents",
                "/robots.txt",
                "has no group for " + ", ".join(unsaid) + "; say allow or disallow for each",
            )
    if re.search(r"nosnippet|max-snippet\s*:\s*0\b", directives):
        say("llm.snippet", "page", "is marked nosnippet, so search and AI answers may not quote it")
    article = str(social.get("type") or "").lower() == "article" or any(
        str(t).endswith(("Article", "BlogPosting")) for t in structured.get("types") or []
    )
    if article and not structured.get("dates") and not int(facts.get("time_tags") or 0):
        say(
            "llm.dates",
            "page",
            "is an article with no datePublished in its structured data and no time element",
        )
    return found


TIMINGS = (
    ("perf.lcp", "lcp_ms", LCP_MS, "its largest content appeared after"),
    ("perf.tbt", "tbt_ms", TBT_MS, "its scripts held the main thread for"),
    ("perf.fcp", "fcp_ms", FCP_MS, "its first content appeared after"),
    ("perf.ttfb", "ttfb_ms", TTFB_MS, "the server took"),
)


def metrics(facts: Mapping[str, Any]) -> dict[str, float]:
    """The numbers of one load worth keeping to compare a later load with."""
    perf: Mapping[str, Any] = facts.get("perf") or {}
    kept = {
        name: float(perf.get(name) or 0)
        for name in (
            "lcp_ms",
            "fcp_ms",
            "ttfb_ms",
            "tbt_ms",
            "cls",
            "requests",
            "script_bytes",
            "style_bytes",
            "image_bytes",
            "font_bytes",
            "total_bytes",
        )
    }
    kept["dom_nodes"] = float(facts.get("dom_nodes") or 0)
    return kept


def slow(
    now: Mapping[str, float],
    before: Mapping[str, float] | None = None,
    sets: Sequence[str] = SETS,
    *,
    width: int = 0,
) -> list[Fault]:
    """Timings of one load past their published limit. With ``before`` (the same page on the
    default branch) only those that are also clearly worse than it: one load is a noisy clock."""
    if "performance" not in sets:
        return []
    found = []
    for rule, name, limit, what in TIMINGS:
        value = float(now.get(name) or 0)
        if value <= limit:
            continue
        if before is not None and value < float(before.get(name) or 0) * (1 + WORSE_SHARE):
            continue
        was = f" (it was {before.get(name, 0) / 1000:.2f} s before)" if before else ""
        found.append(
            Fault(
                rule,
                width,
                "page",
                f"{what} {value / 1000:.2f} s; the limit is {limit / 1000:g} s{was}",
                CHECKS[rule].standard,
            )
        )
    return found


def heavier(
    now: Mapping[str, float],
    before: Mapping[str, float],
    sets: Sequence[str] = SETS,
    *,
    width: int = 0,
) -> list[Fault]:
    """What a page weighs more of than it did on the default branch. Not a published rule: a
    comparison of the project with itself."""
    if "performance" not in sets:
        return []
    said = {
        "script_bytes": "JavaScript",
        "style_bytes": "CSS",
        "image_bytes": "images",
        "font_bytes": "fonts",
        "total_bytes": "the whole page",
        "requests": "requests",
        "dom_nodes": "elements",
    }
    found = []
    for name, floor in HEAVIER_FLOOR.items():
        new, old = float(now.get(name) or 0), float(before.get(name) or 0)
        if new - old < floor or new < old * (1 + HEAVIER_SHARE):
            continue
        if name.endswith("_bytes"):
            detail = f"{said[name]}: {old / 1024:.0f} KB before, {new / 1024:.0f} KB now"
        else:
            detail = f"{said[name]}: {old:.0f} before, {new:.0f} now"
        found.append(Fault("perf.heavier", width, "page", detail))
    return found


def table(now: Mapping[str, float], before: Mapping[str, float] | None = None) -> str:
    """The load's numbers for a person or a reviewer, with the limits and how they were."""
    rows = (
        ("Largest Contentful Paint", "lcp_ms", LCP_MS, "s"),
        ("Cumulative Layout Shift", "cls", CLS, ""),
        ("Total Blocking Time", "tbt_ms", TBT_MS, "s"),
        ("First Contentful Paint", "fcp_ms", FCP_MS, "s"),
        ("Time to First Byte", "ttfb_ms", TTFB_MS, "s"),
        ("JavaScript", "script_bytes", None, "KB"),
        ("Whole page", "total_bytes", None, "KB"),
        ("Requests", "requests", None, "n"),
        ("Elements", "dom_nodes", DOM_NODES, "n"),
    )

    def show(value: float, unit: str) -> str:
        if unit == "s":
            return f"{value / 1000:.2f} s"
        if unit == "KB":
            return f"{value / 1024:.0f} KB"
        return f"{value:.0f}" if unit == "n" else f"{value:.2f}"

    lines = []
    for title, name, limit, unit in rows:
        line = f"- {title}: {show(float(now.get(name) or 0), unit)}"
        if before is not None:
            line += f" (before: {show(float(before.get(name) or 0), unit)})"
        if limit is not None:
            line += f"; limit {show(float(limit), unit)}"
        lines.append(line)
    return "\n".join(lines)


def text_chars(html: str) -> int:
    """How much readable text an HTML document holds before any script runs."""
    body = re.sub(r"(?is)<(script|style|noscript|template|svg|head)\b.*?</\1\s*>", " ", html)
    body = re.sub(r"(?s)<!--.*?-->", " ", body)
    return len(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip())


TEXT_TYPES = re.compile(r"(text/|javascript|json|xml|svg)", re.I)
STATIC_TYPES = re.compile(r"(javascript|css|font|image/)", re.I)


def fetched_facts(
    responses: Iterable[Mapping[str, Any]], *, main_url: str, html: str | None, robots: Any
) -> dict[str, Any]:
    """What the browser was sent, reduced to what :func:`faults` reads. ``responses`` hold
    ``url``, ``status``, ``headers`` (lower-case names) and ``bytes``; ``robots`` is the text of
    ``/robots.txt``, ``None`` when there is none, or left out with ``...`` when it was not asked."""
    seen = list(responses)
    main = next((r for r in seen if r.get("url") == main_url), seen[0] if seen else {})
    headers: Mapping[str, str] = main.get("headers") or {}
    uncompressed, uncached = [], []
    for r in seen:
        head: Mapping[str, str] = r.get("headers") or {}
        kind = head.get("content-type", "")
        if int(r.get("status") or 0) != 200:
            continue
        if (
            TEXT_TYPES.search(kind)
            and int(r.get("bytes") or 0) > COMPRESS_BYTES
            and not head.get("content-encoding")
        ):
            uncompressed.append(str(r.get("url")))
        cache = head.get("cache-control", "")
        if STATIC_TYPES.search(kind) and not re.search(r"max-age=[1-9]|immutable", cache):
            uncached.append(str(r.get("url")))
    out: dict[str, Any] = {
        "status": main.get("status"),
        "x_robots": headers.get("x-robots-tag", ""),
        "uncompressed": uncompressed[:MAX_NAMED],
        "uncached": uncached[:MAX_NAMED],
    }
    if html is not None:
        out["raw_chars"] = text_chars(html)
    if robots is not ...:
        out["robots_txt"] = robots
    return out


def dumps(value: Any) -> str:
    return json.dumps(value, indent=1, sort_keys=True)


# Driving the browser -------------------------------------------------------------------------


@dataclass
class Checked:
    """What one page was found to be: its faults, and the numbers of its load."""

    faults: list[Fault]
    metrics: dict[str, float]


WEIGHED = {"script": "script_bytes", "stylesheet": "style_bytes", "image": "image_bytes"}


def weights(sizes: Iterable[tuple[str, int]]) -> dict[str, int]:
    """What a load weighed by kind, from every request's kind and size on the wire. The page's
    own timing entries leave out what another origin sent, so the browser's record is used."""
    out = {"script_bytes": 0, "style_bytes": 0, "image_bytes": 0, "font_bytes": 0}
    total = count = 0
    for kind, size in sizes:
        name = WEIGHED.get(kind, "font_bytes" if kind == "font" else "")
        if name:
            out[name] += size
        total += size
        count += 1
    return {**out, "total_bytes": total, "requests": count}


def watch(page: Any) -> dict[str, list[Any]]:  # pragma: no cover - needs a browser
    """Before a page loads: install the observers and note everything it is sent."""
    seen: dict[str, list[Any]] = {"responses": [], "sizes": []}
    page.add_init_script(WATCH_JS)

    def on(response: Any) -> None:
        headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
        seen["responses"].append(
            {
                "url": response.url,
                "status": response.status,
                "headers": headers,
                "bytes": int(headers.get("content-length") or 0),
            }
        )

    def done(request: Any) -> None:
        try:
            size = int(request.sizes().get("responseBodySize") or 0)
        except Exception:
            return
        seen["sizes"].append((str(request.resource_type), size))

    page.on("response", on)
    page.on("requestfinished", done)
    return seen


def gather(
    page: Any, seen: dict[str, list[Any]], response: Any
) -> dict[str, Any]:  # pragma: no cover
    """After a page has loaded: what the document says, and what the browser was sent."""
    facts = dict(page.evaluate(FACTS_JS))
    if seen["sizes"]:
        facts["perf"] = {**(facts.get("perf") or {}), **weights(seen["sizes"])}
    html: str | None = None
    try:
        html = response.text() if response is not None else None
    except Exception:
        html = None
    robots: Any = None
    parts = urlsplit(page.url)
    try:
        answer = page.request.get(f"{parts.scheme}://{parts.netloc}/robots.txt", timeout=10_000)
        text = answer.text() if answer.ok else ""
        # A server that answers every path with its application has no robots.txt.
        if text.strip() and not text.lstrip().startswith("<"):
            robots = text
    except Exception:
        robots = ...
    facts["fetched"] = fetched_facts(seen["responses"], main_url=page.url, html=html, robots=robots)
    if response is not None:
        facts["fetched"]["status"] = response.status
    return facts


def check_page(
    url: str,
    *,
    widths: Sequence[int] = (390, 768, 1280),
    wait_ms: int = 500,
    brand: Any = None,
    shots: Any = None,
    name: str = "page",
    sets: Sequence[str] = ("design",),
) -> Checked:
    """Open ``url`` at each width and return everything wrong with it for the standard sets
    asked for: where its elements are (prat.screens), and what the document is."""
    from prat import screens

    pages = screens.measure(
        url, widths=widths, wait_ms=wait_ms, shots=shots, name=name, before=watch, after=gather
    )
    found: list[Fault] = []
    for page in pages:
        if "design" in sets:
            found += screens.faults(page)
        if brand is not None and not brand.empty:
            found += screens.brand_faults(page, brand)
    facts = pages[0].get("facts") if pages else None
    if not facts:
        return Checked(found, {})
    found += faults(facts, sets)
    return Checked(found, metrics(facts))
