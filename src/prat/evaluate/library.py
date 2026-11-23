"""The library of evaluations, and running them.

An evaluation is a named audit of a project: what it looks for, what it needs (the code, or a
running page), and the tools that do the looking. It is never part of building a card. A
person runs one when they want it (on a codebase they have never seen, before a release, after
a piece of work), or chains it into a flow of their own by calling the command there.
"""

from __future__ import annotations

import fnmatch
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from prat.evaluate import pages, tools
from prat.evaluate.model import Finding, Report, ToolRun
from prat.evaluate.tools import NotRun


@dataclass(frozen=True)
class Evaluation:
    name: str
    title: str
    about: str
    # "code": the project's files. "pages": a running page. "both": either, better with both.
    needs: str
    website: bool = False


LIBRARY: dict[str, Evaluation] = {
    e.name: e
    for e in (
        Evaluation(
            "security",
            "Security",
            "Committed secrets, dependencies with known vulnerabilities, code that is unsafe as "
            "written, and the protections a server's answers should carry.",
            "both",
        ),
        Evaluation(
            "accessibility",
            "Accessibility",
            "WCAG 2.2 A and AA on each page, judged by axe-core.",
            "pages",
        ),
        Evaluation(
            "performance",
            "Performance",
            "The Core Web Vitals and what slows a page, judged by Lighthouse.",
            "pages",
        ),
        Evaluation(
            "design",
            "Design",
            "Layout faults on each page at phone and desktop widths: covered or overlapping "
            "controls, cut text, sideways scrolling, small targets, faint text.",
            "pages",
        ),
        Evaluation(
            "seo",
            "Search",
            "What search engines need from each page: titles, descriptions, canonical links, "
            "crawlable links, robots.txt, structured data.",
            "pages",
            website=True,
        ),
        Evaluation(
            "llm",
            "AI search",
            "What AI search products need to find, read and cite each page: crawler access, "
            "content in the server's HTML, snippet controls, dates.",
            "pages",
            website=True,
        ),
    )
}
ALIASES = {
    "a11y": "accessibility",
    "perf": "performance",
    "search": "seo",
    "ai-search": "llm",
    "ai": "llm",
}
GROUPS = {
    "standards": ("design", "accessibility", "performance"),
    "website": ("seo", "llm"),
    "all": tuple(LIBRARY),
}
TITLES = {name: e.title for name, e in LIBRARY.items()}
NEEDS_PAGE = "needs a running page: give --url, or name pages in the project's [screens] table"

# Calls its argument once for each page to look at, while that page can be opened.
Visit = Callable[[Callable[[str], None]], None]


def resolve(names: Sequence[str]) -> list[str]:
    """Evaluation names from what a person typed: names, short names and groups."""
    out: list[str] = []
    for raw in names:
        name = ALIASES.get(raw.strip().lower(), raw.strip().lower())
        expanded = GROUPS.get(name, (name,))
        for one in expanded:
            if one not in LIBRARY:
                known = ", ".join([*LIBRARY, *GROUPS])
                raise ValueError(f"no evaluation named {raw!r} (there are: {known})")
            if one not in out:
                out.append(one)
    return out


class _Runs:
    """Keeps one line per tool of each evaluation across the pages it was run on."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], ToolRun] = {}
        self.findings: list[Finding] = []

    def add(
        self, evaluation: str, tool: str, call: Callable[[], list[Finding]], hint: str = ""
    ) -> None:
        began = time.monotonic()
        before = self.rows.get((evaluation, tool))
        try:
            found = call()
        except NotRun as exc:
            note = f"{exc}; {hint}" if hint else str(exc)
            self.rows[(evaluation, tool)] = before or ToolRun(evaluation, tool, False, note)
            return
        except Exception as exc:  # a tool that fell over is reported, not fatal
            note = f"{type(exc).__name__}: {str(exc)[:200]}"
            self.rows[(evaluation, tool)] = before or ToolRun(evaluation, tool, False, note)
            return
        self.findings += found
        spent = round(time.monotonic() - began + (before.seconds if before else 0), 1)
        count = len(found) + (before.findings if before and before.ran else 0)
        note = before.note if before and before.ran else ""
        self.rows[(evaluation, tool)] = ToolRun(evaluation, tool, True, note, spent, count)

    def note(self, evaluation: str, tool: str, note: str) -> None:
        row = self.rows.get((evaluation, tool))
        if row is not None and row.ran and note:
            self.rows[(evaluation, tool)] = ToolRun(
                evaluation, tool, True, note, row.seconds, row.findings
            )

    def skip(self, evaluation: str, tool: str, why: str) -> None:
        self.rows.setdefault((evaluation, tool), ToolRun(evaluation, tool, False, why))


def run(
    home: Path,
    slug: str,
    names: Sequence[str],
    *,
    root: Path | None = None,
    visit: Visit | None = None,
    target: str = "",
    commit: str = "",
    ignore: Sequence[str] = (),
    said: Callable[[str], None] = lambda text: None,
) -> Report:
    """Run the named evaluations and return the report (not yet saved). ``root`` is the
    project's code, ``visit`` opens its pages; an evaluation that needs the one that is
    missing is reported as not run, with why."""
    began = time.time()
    runs = _Runs()
    if "security" in names:
        if root is None:
            runs.skip("security", "code scanners", "no code to read: evaluated from a URL only")
        else:
            for tool in tools.SECURITY:
                if not tool.applies(root):
                    continue
                said(f"security: {tool.name}")
                runs.add("security", tool.name, lambda t=tool: t.run(root), tool.install)  # type: ignore[misc]
    paged = [n for n in names if LIBRARY[n].needs in ("pages", "both")]
    axe = pages.module(home, "axe-core", "axe.min.js")
    lighthouse = pages.module(home, "lighthouse", "cli/index.js")

    def page(url: str) -> None:
        for name in paged:
            said(f"{name}: {url}")
            if name == "security":
                runs.add(name, "prat headers", lambda: pages.headers(url))
            elif name == "accessibility" and axe is not None:
                runs.add(name, "axe-core", lambda: pages.axe(url, axe))
            elif name == "performance" and lighthouse is not None:
                notes: list[str] = []

                def audit(notes: list[str] = notes) -> list[Finding]:
                    found, note = pages.lighthouse(url, lighthouse)
                    notes.append(note)
                    return found

                runs.add(name, "lighthouse", audit)
                runs.note(name, "lighthouse", "; ".join(n for n in notes if n))
            else:
                runs.add(name, "prat page checks", lambda n=name: pages.own(url, n))  # type: ignore[misc]

    if paged and visit is not None:
        visit(page)
        missing = "not installed, so PRAT's own checks stood in: prat evaluate setup"
        if "accessibility" in paged and axe is None:
            runs.skip("accessibility", "axe-core", missing)
        if "performance" in paged and lighthouse is None:
            runs.skip("performance", "lighthouse", missing)
    for name in paged:
        if visit is None:
            runs.skip(name, "prat headers" if name == "security" else "page checks", NEEDS_PAGE)
    kept = [f for f in runs.findings if not ignored(f.where, ignore)]
    rows = [
        ToolRun(
            row.evaluation,
            row.tool,
            row.ran,
            row.note,
            row.seconds,
            sum(1 for f in kept if (f.evaluation, f.tool) == (row.evaluation, row.tool)),
        )
        for row in runs.rows.values()
    ]
    return Report(
        slug=slug,
        evaluations=list(names),
        target=target,
        commit=commit,
        started=began,
        seconds=round(time.time() - began, 1),
        tools=rows,
        findings=kept,
    )


def ignored(where: str, globs: Sequence[str]) -> bool:
    """Whether a finding is in code the project has said is not its own (``[evaluate]
    ignore``): vendored libraries, generated files, test data."""
    path = re.sub(r":\d+(:\d+)?$", "", where)
    return any(fnmatch.fnmatchcase(path, glob) for glob in globs)
