"""Evaluations of a running page: accessibility, performance, design, search, AI search, and
what a server's answer says about its security.

Where a maintained library exists it does the judging: axe-core for accessibility, Lighthouse
for performance. Both are installed once into ``$PRAT_HOME/tools`` with npm (``prat evaluate
setup``). Without them PRAT's own measurements (prat.pagecheck) stand in and the report says
so. Design, search and AI search have no such library and are PRAT's own checks against the
standard sets it ships (prat.standards).
"""

# ruff: noqa: E501  (the sentences findings are reported in read better whole)
from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from prat import pagecheck, screens, standards
from prat.evaluate.model import Finding
from prat.evaluate.tools import TIMEOUT, NotRun

TOOLS_DIR = "tools"
PACKAGES = ("axe-core@4", "lighthouse@12")
# The WCAG levels PRAT's accessibility set covers, and the accepted practices beside them.
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]
AXE_SEVERITY = {"critical": "high", "serious": "high", "moderate": "medium", "minor": "low"}
# PRAT's own page checks: the ones that stop a person or a crawler outright are high.
HIGH = frozenset(
    {
        "covered", "overlap", "a11y.control_name", "a11y.image_alt", "a11y.hidden_focusable",
        "a11y.zoom", "design.field_label", "seo.noindex", "seo.robots_txt", "seo.status",
        "seo.links", "llm.raw_content", "llm.agents", "perf.lcp", "perf.cls",
    }
)  # fmt: skip
LOW = frozenset(
    {"seo.icon", "seo.social", "seo.hreflang", "llm.training_agents", "perf.lcp_priority",
     "perf.lazy_offscreen", "a11y.tabindex", "off_brand"}
)  # fmt: skip
SET_OF = {"a11y": "accessibility", "perf": "performance", "seo": "seo", "llm": "llm"}


def tools_dir(home: Path) -> Path:
    return home / TOOLS_DIR


def setup(home: Path) -> str:
    """Install the page libraries with npm. Returns what npm said."""
    where = tools_dir(home)
    where.mkdir(parents=True, exist_ok=True)
    npm = "npm.cmd" if os.name == "nt" else "npm"
    try:
        done = subprocess.run(
            [npm, "install", "--prefix", str(where), "--no-fund", "--no-audit", *PACKAGES],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise NotRun(f"npm could not install them: {exc}") from exc
    if done.returncode != 0:
        raise NotRun(f"npm failed: {(done.stderr or done.stdout).strip()[-400:]}")
    return (done.stdout or "").strip()


def module(home: Path, name: str, file: str) -> Path | None:
    path = tools_dir(home) / "node_modules" / name / file
    return path if path.is_file() else None


def severity_of(rule: str) -> str:
    return "high" if rule in HIGH else "low" if rule in LOW else "medium"


def from_faults(
    found: Sequence[screens.Fault], page: str, only: str | None = None
) -> list[Finding]:
    """PRAT's own page faults as findings, each under the evaluation its rule belongs to."""
    out = []
    for fault in screens.merged(found):
        check = pagecheck.CHECKS.get(fault.rule)
        rule = standards.find(check.rule_id, check.set) if check else None
        out.append(
            Finding(
                only or (check.set if check else "design"),
                fault.rule,
                severity_of(fault.rule),
                pagecheck.SAID.get(fault.rule) or screens.SAID.get(fault.rule) or fault.rule,
                f"{page} {fault.element}".strip(),
                fault.detail,
                rule.do_not if rule else "",
                fault.standard,
                "prat page checks",
            )
        )
    return out


def own(url: str, evaluation: str, widths: Sequence[int] = (390, 1280)) -> list[Finding]:
    """One evaluation of one page with PRAT's own checks."""
    checked = pagecheck.check_page(url, widths=widths, sets=(evaluation,))
    found = list(checked.faults)
    if evaluation == "performance":
        found += pagecheck.slow(checked.metrics, None, (evaluation,))
    return from_faults(found, url, evaluation)


def axe(url: str, script: Path, *, width: int = 1280, wait_ms: int = 500) -> list[Finding]:
    """axe-core's violations on one page."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found,unused-ignore]
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise NotRun("Playwright is not installed: pip install 'prat[screens]'") from exc
    with sync_playwright() as play:  # pragma: no cover - needs a browser
        browser = play.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": screens.HEIGHT})
            page.goto(url, wait_until="networkidle", timeout=30_000)
            page.wait_for_timeout(wait_ms)
            page.add_script_tag(path=str(script))
            result = page.evaluate(
                "tags => axe.run(document, {runOnly: {type: 'tag', values: tags}})", AXE_TAGS
            )
        finally:
            browser.close()
    return axe_findings(result, url)


def axe_findings(result: Mapping[str, Any], page: str) -> list[Finding]:
    found = []
    for violation in result.get("violations") or []:
        tags = [t for t in violation.get("tags") or [] if re.fullmatch(r"wcag\d{3,4}", str(t))]
        criteria = ", ".join(".".join(t[4:]) for t in tags)
        for node in violation.get("nodes") or []:
            target = " ".join(str(t) for t in node.get("target") or [])
            said = " ".join(str(node.get("failureSummary") or "").split())
            found.append(
                Finding(
                    "accessibility",
                    str(violation.get("id")),
                    AXE_SEVERITY.get(str(node.get("impact") or violation.get("impact")), "medium"),
                    str(violation.get("help") or violation.get("id")),
                    f"{page} {target}".strip(),
                    said[:300],
                    str(violation.get("helpUrl") or ""),
                    f"WCAG 2.2 {criteria}" if criteria else "accepted practice (axe-core)",
                    "axe-core",
                )
            )
    return found


def chrome() -> str:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found,unused-ignore]
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise NotRun("Playwright is not installed: pip install 'prat[screens]'") from exc
    with sync_playwright() as play:  # pragma: no cover - needs a browser
        return str(play.chromium.executable_path)


def lighthouse(url: str, cli: Path) -> tuple[list[Finding], str]:
    """Lighthouse's performance audits of one page, and its score as a note."""
    try:
        done = subprocess.run(
            ["node", str(cli), url, "--output=json", "--quiet", "--only-categories=performance",
             "--chrome-flags=--headless=new --no-sandbox"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
            check=False,
            env={**os.environ, "CHROME_PATH": chrome()},
        )  # fmt: skip
    except FileNotFoundError as exc:
        raise NotRun("Node.js is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise NotRun("Lighthouse did not finish") from exc
    try:
        report = json.loads(done.stdout)
    except ValueError as exc:
        raise NotRun(f"Lighthouse failed: {(done.stderr or '').strip()[-300:]}") from exc
    return lighthouse_findings(report, url)


def lighthouse_findings(report: Mapping[str, Any], page: str) -> tuple[list[Finding], str]:
    category = (report.get("categories") or {}).get("performance") or {}
    audits: Mapping[str, Any] = report.get("audits") or {}
    found = []
    for ref in category.get("auditRefs") or []:
        audit = audits.get(str(ref.get("id"))) or {}
        score = audit.get("score")
        if score is None or float(score) >= 0.9:
            continue
        if audit.get("scoreDisplayMode") in ("informative", "notApplicable", "manual"):
            continue
        metric = ref.get("group") == "metrics"
        about = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", str(audit.get("description") or ""))
        found.append(
            Finding(
                "performance",
                str(audit.get("id")),
                "high"
                if float(score) < 0.5 and metric
                else "medium"
                if float(score) < 0.5
                else "low",
                str(audit.get("title") or audit.get("id")),
                page,
                str(audit.get("displayValue") or "").replace(chr(0xA0), " "),
                about[:400],
                "Core Web Vitals (lab)" if metric else "Lighthouse",
                "lighthouse",
            )
        )
    score = category.get("score")
    return found, f"performance score {round(float(score) * 100)}" if score is not None else ""


# What the server's answer says about its security --------------------------------------------

HEADERS = "OWASP Secure Headers Project"


def header_findings(url: str, headers: Mapping[str, str], cookies: Sequence[str]) -> list[Finding]:
    """Protections a response should carry and does not. ``headers`` have lower-case names;
    ``cookies`` are the raw Set-Cookie values."""
    found = []

    def say(rule: str, severity: str, title: str, detail: str, fix: str) -> None:
        found.append(
            Finding("security", rule, severity, title, url, detail, fix, HEADERS, "prat headers")
        )

    policy = headers.get("content-security-policy", "")
    if url.startswith("https://") and "strict-transport-security" not in headers:
        say("header-hsts", "medium", "No Strict-Transport-Security header",
            "a browser may be sent to the plain HTTP site",
            "Send Strict-Transport-Security: max-age=31536000; includeSubDomains.")  # fmt: skip
    if not policy:
        say("header-csp", "medium", "No Content-Security-Policy header",
            "nothing limits which scripts the page may run",
            "Send a Content-Security-Policy that names the script sources the page uses.")  # fmt: skip
    if headers.get("x-content-type-options", "").lower() != "nosniff":
        say("header-nosniff", "low", "No X-Content-Type-Options: nosniff header",
            "a browser may guess a file's type", "Send X-Content-Type-Options: nosniff.")  # fmt: skip
    if "x-frame-options" not in headers and "frame-ancestors" not in policy:
        say("header-framing", "medium", "The page can be framed by any site",
            "no X-Frame-Options header and no frame-ancestors in the policy",
            "Send Content-Security-Policy: frame-ancestors 'self' (or X-Frame-Options: DENY).")  # fmt: skip
    if "referrer-policy" not in headers:
        say("header-referrer", "low", "No Referrer-Policy header",
            "full addresses may be sent to other sites",
            "Send Referrer-Policy: strict-origin-when-cross-origin.")  # fmt: skip
    for raw in cookies:
        name = raw.split("=", 1)[0].strip()
        flags = raw.lower()
        missing = [
            flag
            for flag, present in (
                ("Secure", "; secure" in flags or not url.startswith("https://")),
                ("HttpOnly", "; httponly" in flags),
                ("SameSite", "; samesite=" in flags),
            )
            if not present
        ]
        if missing:
            say("cookie-flags", "medium", "A cookie is set without its protections",
                f"{name} lacks {', '.join(missing)}",
                "Set Secure, HttpOnly and SameSite on every cookie that is not read by scripts.")  # fmt: skip
    return found


def headers(url: str) -> list[Finding]:
    import httpx

    try:
        response = httpx.get(url, follow_redirects=True, timeout=20)
    except httpx.HTTPError as exc:
        raise NotRun(f"could not fetch {url}: {exc}") from exc
    sent = {k.lower(): v for k, v in response.headers.items()}
    return header_findings(str(response.url), sent, response.headers.get_list("set-cookie"))
