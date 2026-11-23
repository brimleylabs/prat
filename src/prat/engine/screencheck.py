"""A card's screens, looked at as its builder ends its turn.

When a card changes interface code and its project says where its screens are (``[screens]``
in the project file), PRAT starts the project's environment on the card's work, opens each
page at each width (prat.screens) and hands the builder what is broken on them, in the same
session and before a reviewer or a person sees it.

Only what the card brought is handed back. The same pages are measured once on the default
branch, in the agent clone, and kept by commit under ``$PRAT_HOME/screens``; a fault already
there is not this card's to fix. When that first look cannot be had, the faults are reported
with a note that some may be older than the card.

Pictures of every page at every width are kept in the run's folder, with a note for the
reviewer that lists them and the questions to ask of them (prat.standards).

A page is judged against the standard sets that apply to the project (prat.pagecheck): design,
accessibility and performance always, search and AI search when the project is a public
website. The numbers of each page's load are kept with the default branch's look too, so a
card is told when it made a page heavier, and told about a slow timing only when the page was
not already that slow.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prat import pagecheck, screens, standards
from prat.environments import EnvironmentError, EnvironmentManager
from prat.projects import ProjectSpec, start_order

KNOWN_DIR = "screens"
NOTE = "review.md"
# The card id the default branch's own look runs under, so it never meets a card's preview.
BASELINE_CARD = 0
# Rules whose examples are spelled out for a builder that broke them, at most.
MAX_EXAMPLES = 6

# Returns a page's faults, or a prat.pagecheck.Checked with the numbers of its load as well.
Opener = Callable[..., Any]


@dataclass
class Look:
    """What one look at a project's pages found."""

    looked: bool = False
    reason: str = ""
    # page path -> its faults
    faults: dict[str, list[screens.Fault]] = field(default_factory=dict)
    # page path -> the numbers of its load (prat.pagecheck.metrics)
    metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def count(self) -> int:
        return sum(len(found) for found in self.faults.values())

    def keys(self) -> list[str]:
        return sorted(
            {f"{page}|{screens.key(f)}" for page, found in self.faults.items() for f in found}
        )


@dataclass
class Known:
    """The default branch's own look: the faults already there, and its pages' numbers."""

    keys: list[str] = field(default_factory=list)
    metrics: dict[str, dict[str, float]] = field(default_factory=dict)


def sets_of(spec: ProjectSpec) -> tuple[str, ...]:
    """The standard sets this project's pages are held to."""
    wanted = spec.screens
    if wanted is None:
        return ()
    return pagecheck.sets_for(website=wanted.website, chosen=wanted.checks)


def page_name(page: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", page.lower()).strip("-") or "home"


def load_brand(base: Path, spec: ProjectSpec) -> screens.Brand | None:
    wanted = spec.screens.brand if spec.screens else None
    if not wanted:
        return None
    try:
        return screens.brand_from(tomllib.loads((base / wanted).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def look(
    manager: EnvironmentManager,
    slug: str,
    spec: ProjectSpec,
    base: Path,
    card_id: int,
    variables: Mapping[str, str],
    *,
    shots: Path | None = None,
    stop: Callable[[], bool] = lambda: False,
    opener: Opener = pagecheck.check_page,
) -> Look:
    """Start the screens' environment on ``base`` (and the ones it needs), open every page,
    and stop what was started."""
    # Imported here: prat.setup_agent imports the engine's own modules.
    from prat.setup_agent import _check_one

    wanted = spec.screens
    if wanted is None or not wanted.environment or not wanted.pages:
        return Look(reason="the project names no pages to open")
    env = next((e for e in spec.environment if e.name == wanted.environment), None)
    if env is None:
        return Look(reason=f"no environment named {wanted.environment}")
    began = time.monotonic()
    result = Look()
    running = []
    try:
        for need in start_order(list(spec.environment), [env.name]):
            check = _check_one(manager, slug, need, base, card_id, variables, stop)
            if manager.view(slug, need, card_id=card_id).running:
                running.append(need)
            if not check.passed:
                result.reason = f"{need.name} {check.detail}"
                return result
        port = manager.view(slug, env, card_id=card_id).port
        if port is None:
            result.reason = f"{env.name} has no port to open"
            return result
        brand = load_brand(base, spec)
        for page in wanted.pages:
            if stop():
                result.reason = "stopped"
                return result
            url = f"http://127.0.0.1:{port}/{page.lstrip('/')}"
            checked = opener(
                url,
                widths=wanted.widths,
                wait_ms=wanted.wait_ms,
                brand=brand,
                shots=shots,
                name=page_name(page),
                sets=sets_of(spec),
            )
            if isinstance(checked, pagecheck.Checked):
                result.faults[page] = checked.faults
                if checked.metrics:
                    result.metrics[page] = checked.metrics
            else:
                result.faults[page] = list(checked)
        result.looked = True
        return result
    except (EnvironmentError, RuntimeError, ValueError, OSError) as exc:
        result.reason = str(exc)[:300]
        return result
    except Exception as exc:  # a browser that fell over is not the card's fault
        result.reason = f"{type(exc).__name__}: {exc}"[:300]
        return result
    finally:
        for started in reversed(running):
            with contextlib.suppress(EnvironmentError):
                manager.stop(slug, started, card_id=card_id, extra_env=variables)
        result.seconds = round(time.monotonic() - began, 1)


def known_path(home: Path, slug: str, commit: str) -> Path:
    return home / KNOWN_DIR / slug / f"known-{commit[:16]}.json"


def load_known(home: Path, slug: str, commit: str) -> Known | None:
    try:
        data = json.loads(known_path(home, slug, commit).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, list):  # a look kept before pages' numbers were
        return Known([str(item) for item in data])
    if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
        return None
    numbers = data.get("metrics")
    return Known(
        [str(item) for item in data["keys"]],
        {
            str(page): {str(k): float(v) for k, v in values.items()}
            for page, values in (numbers if isinstance(numbers, dict) else {}).items()
            if isinstance(values, dict)
        },
    )


def save_known(home: Path, slug: str, commit: str, known: Known) -> None:
    path = known_path(home, slug, commit)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Only the newest few looks are worth their space.
    for old in sorted(path.parent.glob("known-*.json"), key=lambda p: p.stat().st_mtime)[:-4]:
        with contextlib.suppress(OSError):
            old.unlink()
    path.write_text(
        json.dumps({"keys": known.keys, "metrics": known.metrics}, indent=1), encoding="utf-8"
    )


def new_faults(
    found: Look, known: Known | None, sets: tuple[str, ...] = ()
) -> dict[str, list[screens.Fault]]:
    """The faults of a look that were not there before, by page. With the default branch's
    numbers, a page that is heavier or clearly slower than it was is the card's fault too."""
    before = set(known.keys if known else ())
    fresh = {
        page: [f for f in faults if f"{page}|{screens.key(f)}" not in before]
        for page, faults in found.faults.items()
    }
    for page, now in found.metrics.items():
        was = known.metrics.get(page) if known else None
        if was:
            fresh[page] = [
                *fresh.get(page, []),
                *pagecheck.heavier(now, was, sets),
                *pagecheck.slow(now, was, sets),
            ]
    return {page: faults for page, faults in fresh.items() if faults}


def examples(fresh: Mapping[str, list[screens.Fault]]) -> str:
    """The published rule behind each kind of fault found, with pages that show it done well."""
    rules: list[str] = []
    for faults in fresh.values():
        for fault in faults:
            if fault.rule in pagecheck.CHECKS and fault.rule not in rules:
                rules.append(fault.rule)
    told = []
    for rule in rules[:MAX_EXAMPLES]:
        check = pagecheck.CHECKS[rule]
        text = standards.example_for(standards.find(check.rule_id, check.set))
        if text:
            told.append(text)
    if not told:
        return ""
    return "\n\n".join(["The rules behind these, with pages that show them done well:", *told])


def feedback(fresh: Mapping[str, list[screens.Fault]], *, compared: bool) -> str:
    """What the builder is told about its screens."""
    lines = [
        "Before you finish: PRAT opened the pages this card touches and measured them. These "
        "faults are on them now"
        + (
            " and were not there before your change."
            if compared
            else " (the pages could not be compared with how they were, so some may be older "
            "than your change: fix the ones your change touches)."
        ),
        "Fix each in the code, then finish again. Each names the element, what is wrong and "
        "the screen widths it was seen at.",
    ]
    for page, faults in fresh.items():
        lines += ["", screens.report(faults, page, said=pagecheck.SAID, what="Faults")]
    shown = examples(fresh)
    if shown:
        lines += ["", shown]
    return "\n".join(lines)


def review_note(
    shots: Path,
    found: Look,
    fresh: Mapping[str, list[screens.Fault]],
    *,
    sets: tuple[str, ...] = ("design",),
    known: Known | None = None,
    asked: tuple[str, ...] | None = None,
) -> str:
    """What the reviewer is told: where the pictures are, what is still wrong, what to ask."""
    pictures = sorted(p.name for p in shots.glob("*.png")) if shots.is_dir() else []
    lines = ["## The screens this card changed"]
    if pictures:
        lines += [
            f"Pictures of each page at each width are in {shots} (read them with the Read "
            "tool): " + ", ".join(pictures) + "."
        ]
    left = sum(len(f) for f in fresh.values())
    if left:
        lines += ["", f"Measured faults still on the pages after the builder's last turn ({left}):"]
        for page, faults in fresh.items():
            lines += [screens.report(faults, page, said=pagecheck.SAID, what="Faults")]
    elif found.looked:
        lines += ["", "PRAT measured the pages and found no new fault on them."]
    if found.metrics and "performance" in sets:
        lines += [
            "",
            "How each page loaded, once, on this machine. The timings are a lab measurement and "
            "real visitors will see slower ones; the weights are exact.",
        ]
        for page, now in found.metrics.items():
            lines += [page, pagecheck.table(now, known.metrics.get(page) if known else None)]
    # The sets a reviewer is asked the unmeasured rules of: all of them unless told otherwise.
    checklist = standards.reviewer_text(sets if asked is None else asked)
    if checklist:
        lines += ["", checklist]
    return "\n".join(lines)


def write_note(run_dir: Path, text: str) -> Path:
    path = run_dir / KNOWN_DIR / NOTE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def read_note(run_dir: Path) -> str:
    try:
        return (run_dir / KNOWN_DIR / NOTE).read_text(encoding="utf-8")
    except OSError:
        return ""


def summary(
    found: Look, fresh: Mapping[str, list[screens.Fault]], compared: bool
) -> dict[str, Any]:
    return {
        "looked": found.looked,
        "reason": found.reason,
        "seconds": found.seconds,
        "pages": len(found.faults),
        "faults": found.count,
        "new": sum(len(f) for f in fresh.values()),
        "compared": compared,
        "rules": sorted({f.rule for faults in fresh.values() for f in faults}),
    }
