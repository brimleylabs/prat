"""Evaluations: findings, reports, the tools' answers read, and the library (prat.evaluate)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from prat.evaluate import library, model, pages, tools
from prat.evaluate.model import Finding, Report


def finding(
    rule: str = "code-B602", severity: str = "high", where: str = "app/run.py:12"
) -> Finding:
    return Finding("security", rule, severity, "shell equals true", where, "a call", "Do not.")


def test_a_finding_is_the_same_finding_when_its_line_moves() -> None:
    assert finding(where="app/run.py:12").key == finding(where="app/run.py:40").key
    assert finding(where="app/run.py:12").key != finding(where="app/other.py:12").key
    assert model.rank("critical") < model.rank("high") < model.rank("nonsense")
    assert model.at_least("critical", "high") and not model.at_least("medium", "high")


def test_reports_are_kept_and_compared_with_the_one_before(tmp_path: Path) -> None:
    assert model.load(tmp_path, "shop") is None
    first = Report("shop", ["security"], "repo", id="20261101-090000")
    first.findings = [finding(), finding("dependency-lodash", "critical", "package-lock.json")]
    model.save(tmp_path, first)
    second = Report("shop", ["security", "design"], "repo", id="20261102-090000")
    second.findings = [
        finding(where="app/run.py:90"),
        finding("secret-aws", "critical", "settings.py:3"),
        Finding("design", "contrast", "medium", "Text too faint", "/ p.hint"),
    ]
    model.save(tmp_path, second)
    assert model.ids(tmp_path, "shop") == ["20261101-090000", "20261102-090000"]
    assert (
        model.load(tmp_path, "shop") == second and model.load(tmp_path, "shop", first.id) == first
    )
    compared = model.compare(tmp_path, second)
    assert compared.against == ["security"]  # design has nothing earlier to compare with
    assert [f.rule for f in compared.new] == ["secret-aws"]
    assert [f.rule for f in compared.still] == ["code-B602"]
    assert [f.rule for f in compared.fixed] == ["dependency-lodash"]
    assert [f.rule for f in model.worst_new(second, compared)] == ["secret-aws"]
    assert len(model.worst_new(first, model.compare(tmp_path, first))) == 1  # a first run: all
    text = model.render(second, compared, titles=library.TITLES)
    assert "## Security: 2 findings" in text and "1 critical, 1 high" in text
    assert text.index("CRITICAL") < text.index("HIGH  shell equals true (1)")
    assert "Since the last report (Security): 1 new, 1 fixed, 1 still open." in text
    assert "Text too faint" not in model.render(second, floor="high")


def test_a_report_says_which_tools_did_not_run_and_caps_long_lists() -> None:
    report = Report("shop", ["security"], "repo", id="1")
    report.tools = [
        model.ToolRun("security", "bandit", True, "", 2.0, 30),
        model.ToolRun("security", "semgrep", False, "semgrep is not installed; see the site"),
    ]
    report.findings = [finding(where=f"a/f{i}.py:1") for i in range(30)]
    text = model.render(report)
    assert "- bandit: 30 findings" in text
    assert "- semgrep: NOT RUN (semgrep is not installed; see the site)" in text
    assert "and 18 more" in text and "Fix: Do not." in text
    assert "and 18 more" not in model.render(report, shown=100)


def test_secrets_in_tracked_files_are_critical_and_in_tests_are_not(tmp_path: Path) -> None:
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    (tmp_path / "settings.py").write_text(f"x = 1\nKEY = '{key}'\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_keys.py").write_text(f"FAKE = '{key}'\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text(f"k = '{key}'\n", encoding="utf-8")
    found = sorted(tools.secrets(tmp_path), key=lambda f: f.where)
    assert [(f.where, f.severity) for f in found] == [
        ("settings.py:2", "critical"),
        ("tests/test_keys.py:1", "low"),
    ]
    assert key not in found[0].detail and found[0].rule != found[1].rule


def answers(monkeypatch: pytest.MonkeyPatch, text: str) -> list[list[str]]:
    """Replace running a tool: it answers ``text``. Returns the commands asked for."""
    asked: list[list[str]] = []

    def run(argv: Any, cwd: Path, **more: Any) -> str:
        asked.append(list(argv))
        return text

    monkeypatch.setattr(tools, "_run", run)
    monkeypatch.setattr(tools.util, "find_spec", lambda name: object())
    return asked


def test_npm_audit_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "package-lock.json").write_text("{}", encoding="utf-8")
    audit = {
        "vulnerabilities": {
            "lodash": {
                "severity": "moderate",
                "range": "<4.17.21",
                "via": [{"title": "Prototype pollution"}],
                "fixAvailable": {"name": "lodash", "version": "4.17.21"},
            },
            "left": {"severity": "critical", "via": ["lodash"], "fixAvailable": False},
        }
    }
    asked = answers(monkeypatch, json.dumps(audit))
    found = {f.rule: f for f in tools.npm_audit(tmp_path)}
    assert asked == [["npm", "audit", "--json"]]
    assert found["dependency-lodash"].severity == "medium"
    assert found["dependency-lodash"].where == "web/package-lock.json"
    assert "Prototype pollution" in found["dependency-lodash"].detail
    assert found["dependency-lodash"].fix == "Upgrade to lodash 4.17.21."
    assert found["dependency-left"].severity == "critical"
    assert "No fixed version" in found["dependency-left"].fix


def test_pip_audit_and_bandit_are_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "requirements.txt").write_text("jinja2==2.0\n", encoding="utf-8")
    vuln = {"id": "PYSEC-1", "fix_versions": ["3.1.4"], "description": "Sandbox escape"}
    answers(
        monkeypatch,
        "a line the tool prints first\n"
        + json.dumps({"dependencies": [{"name": "jinja2", "version": "2.0", "vulns": [vuln]}]}),
    )
    (found,) = tools.pip_audit(tmp_path)
    assert (found.rule, found.fix) == ("dependency-jinja2", "Upgrade to 3.1.4 or later.")
    assert "1 advisories (PYSEC-1). Sandbox escape" in found.detail

    def result(
        file: str, severity: str, confidence: str, name: str = "exec_used"
    ) -> dict[str, Any]:
        return {
            "filename": file,
            "line_number": 7,
            "issue_severity": severity,
            "issue_confidence": confidence,
            "issue_text": "Use of eval. It is unsafe.",
            "test_id": "B307",
            "test_name": name,
            "more_info": "https://b.example/b307",
        }

    results = [
        result(".\\app\\run.py", "MEDIUM", "HIGH", "blacklist"),
        result("./tests/test_run.py", "HIGH", "HIGH"),
        result("./app/low.py", "LOW", "MEDIUM"),
    ]
    asked = answers(monkeypatch, json.dumps({"results": results}))
    (kept,) = tools.bandit(tmp_path)
    assert (kept.where, kept.severity, kept.title) == ("app/run.py:7", "medium", "Use of eval")
    assert "B101" in asked[0]  # asserts are how tests are written
    monkeypatch.setattr(tools.util, "find_spec", lambda name: None)
    with pytest.raises(tools.NotRun, match="not installed"):
        tools.bandit(tmp_path)


def test_a_servers_answer_is_checked_for_its_protections() -> None:
    bare = pages.header_findings("https://shop.example/", {}, ["sid=abc; Path=/"])
    assert {f.rule for f in bare} == {
        "header-hsts",
        "header-csp",
        "header-nosniff",
        "header-framing",
        "header-referrer",
        "cookie-flags",
    }
    assert (
        "sid lacks Secure, HttpOnly, SameSite"
        in next(f for f in bare if f.rule == "cookie-flags").detail
    )
    good = {
        "strict-transport-security": "max-age=31536000",
        "content-security-policy": "default-src 'self'; frame-ancestors 'self'",
        "x-content-type-options": "nosniff",
        "referrer-policy": "strict-origin-when-cross-origin",
    }
    cookie = "sid=abc; Secure; HttpOnly; SameSite=Lax"
    assert pages.header_findings("https://shop.example/", good, [cookie]) == []
    # A site served over plain HTTP on this machine is not asked for HSTS or Secure cookies.
    local = pages.header_findings(
        "http://127.0.0.1:3000/", good, ["sid=abc; HttpOnly; SameSite=Lax"]
    )
    assert local == []


def test_axe_and_lighthouse_answers_are_read() -> None:
    axe = {
        "violations": [
            {
                "id": "image-alt",
                "impact": "critical",
                "help": "Images must have alternative text",
                "helpUrl": "https://d.example/image-alt",
                "tags": ["cat.text-alternatives", "wcag2a", "wcag111"],
                "nodes": [
                    {"target": ["img.hero"], "failureSummary": "Fix any:\n  no alt"},
                    {"target": ["img.logo"], "impact": "minor", "failureSummary": "no alt"},
                ],
            }
        ]
    }
    found = pages.axe_findings(axe, "/home")
    assert [(f.where, f.severity) for f in found] == [
        ("/home img.hero", "high"),
        ("/home img.logo", "low"),
    ]
    assert found[0].standard == "WCAG 2.2 1.1.1" and found[0].detail == "Fix any: no alt"
    report = {
        "categories": {
            "performance": {
                "score": 0.42,
                "auditRefs": [
                    {"id": "largest-contentful-paint", "group": "metrics"},
                    {"id": "render-blocking-resources"},
                    {"id": "fine"},
                    {"id": "notes"},
                ],
            }
        },
        "audits": {
            "largest-contentful-paint": {
                "id": "largest-contentful-paint",
                "title": "Largest Contentful Paint",
                "score": 0.2,
                "displayValue": "6.1" + chr(0xA0) + "s",
                "description": "See [the guide](https://w.example/lcp).",
            },
            "render-blocking-resources": {
                "id": "render-blocking-resources",
                "title": "Eliminate render-blocking resources",
                "score": 0.6,
            },
            "fine": {"id": "fine", "title": "Fine", "score": 1},
            "notes": {
                "id": "notes",
                "title": "Notes",
                "score": 0,
                "scoreDisplayMode": "informative",
            },
        },
    }
    audits, note = pages.lighthouse_findings(report, "/home")
    assert note == "performance score 42"
    assert [(f.rule, f.severity) for f in audits] == [
        ("largest-contentful-paint", "high"),
        ("render-blocking-resources", "low"),
    ]
    assert audits[0].detail == "6.1 s" and audits[0].fix == "See the guide (https://w.example/lcp)."


def test_names_short_names_and_groups_resolve() -> None:
    assert library.resolve(["Security", "a11y"]) == ["security", "accessibility"]
    assert library.resolve(["standards", "design"]) == ["design", "accessibility", "performance"]
    assert library.resolve(["ai-search"]) == ["llm"] and len(library.resolve(["all"])) == 6
    with pytest.raises(ValueError, match="no evaluation named 'speed'"):
        library.resolve(["speed"])
    assert library.ignored("vendor/lib/a.py:12", ["vendor/*"])
    assert not library.ignored("app/a.py:12", ["vendor/*"])


def test_an_evaluation_reports_what_it_could_not_look_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def tool(name: str, run: Callable[[Path], list[Finding]]) -> tools.Tool:
        return tools.Tool(name, "", "install it", lambda root: True, run)

    def missing(root: Path) -> list[Finding]:
        raise tools.NotRun("scanner is not installed")

    def broken(root: Path) -> list[Finding]:
        raise RuntimeError("fell over")

    def works(root: Path) -> list[Finding]:
        return [
            Finding("security", "r", "high", "t", "app/a.py:1", tool="works"),
            Finding("security", "r", "high", "t", "vendor/b.py:1", tool="works"),
        ]

    monkeypatch.setattr(
        tools, "SECURITY", (tool("works", works), tool("missing", missing), tool("broken", broken))
    )
    report = library.run(
        tmp_path, "shop", ["security", "design"], root=tmp_path, ignore=["vendor/*"]
    )
    rows = {t.tool: t for t in report.tools}
    assert rows["works"].ran and rows["works"].findings == 1  # the vendored one is left out
    assert (
        not rows["missing"].ran and rows["missing"].note == "scanner is not installed; install it"
    )
    assert not rows["broken"].ran and "RuntimeError: fell over" in rows["broken"].note
    assert not rows["prat headers"].ran and "needs a running page" in rows["page checks"].note
    assert [f.where for f in report.findings] == ["app/a.py:1"]

    seen: list[str] = []
    monkeypatch.setattr(pages, "headers", lambda url: [])
    monkeypatch.setattr(pages, "own", lambda url, name: seen.append(f"{name} {url}") or [])
    paged = library.run(
        tmp_path, "shop", ["security", "design"], visit=lambda page: [page("/"), page("/about")][0]
    )
    assert seen == ["design /", "design /about"]
    notes = {t.tool: t for t in paged.tools}
    assert "no code to read" in notes["code scanners"].note and notes["prat headers"].ran


def test_gitleaks_answers_are_read_and_test_files_are_not_critical() -> None:
    leaks = [
        {
            "RuleID": "aws-access-token",
            "Description": "Identified a pattern that may indicate AWS credentials, risking it.",
            "File": r"app\settings.py",
            "StartLine": 3,
            "Commit": "f1510388e0571234",
        },
        {"RuleID": "generic-api-key", "File": "tests/unit/test_errors.py", "StartLine": 49},
    ]
    real, fixture = tools.gitleaks_findings(leaks)
    assert (real.where, real.severity, real.rule) == (
        "app/settings.py:3",
        "critical",
        "secret-aws-access-token",
    )
    assert real.title == "Identified a pattern that may indicate AWS credentials"
    assert real.detail == "commit f1510388e057"
    assert (fixture.severity, fixture.rule) == ("low", "secret-generic-api-key-in-tests")
    assert "in a test file" in fixture.title
