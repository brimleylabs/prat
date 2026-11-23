"""The tools a security evaluation runs over a codebase, and how each one's report is read.

PRAT does not write its own scanners where a maintained one exists. Each tool here is found on
the machine, run with a time limit in the project's folder, and its own JSON read into
findings. A tool that is not installed is not a failure: the report says it did not run and
what would install it, so a person sees what was and was not looked at.

Only the secret scan is PRAT's own (prat.policy.secrets, the patterns that already guard every
agent's writes); ``gitleaks`` replaces it when it is installed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import util
from pathlib import Path
from typing import Any

from prat.evaluate.model import Finding
from prat.policy.secrets import find_secrets

TIMEOUT = 600.0
SKIP_DIRS = ("node_modules", ".venv", "venv", "vendor", ".git", "dist", "build", "__pycache__")
MAX_FILE_BYTES = 1_000_000
SEARCH_DEPTH = 3
TEST_FILE = re.compile(
    r"(^|/)(tests?|__tests__|fixtures|spec)/|(^|/)test_[^/]*$|[._](test|spec)\.[a-z]+$"
)


class NotRun(Exception):
    """The tool could not be used here; the message says why and what would let it."""


@dataclass(frozen=True)
class Tool:
    name: str
    about: str
    install: str
    applies: Callable[[Path], bool]
    run: Callable[[Path], list[Finding]]


def _run(argv: Sequence[str], cwd: Path, *, ok: Sequence[int] = (0, 1)) -> str:
    """A tool's standard output. Scanners exit 1 when they find something, which is not an
    error; anything else is."""
    try:
        done = subprocess.run(
            list(argv),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise NotRun(f"{argv[0]} is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise NotRun(f"{argv[0]} did not finish in {TIMEOUT:.0f} s") from exc
    if done.returncode not in ok and not done.stdout.strip():
        raise NotRun(f"{argv[0]} failed: {(done.stderr or '').strip()[-300:]}")
    return done.stdout


def _json(text: str, tool: str) -> Any:
    # Some tools print a line of their own before the JSON.
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    try:
        return json.loads(text[start:]) if start >= 0 else {}
    except ValueError as exc:
        raise NotRun(f"{tool} gave an answer that could not be read") from exc


def _files(root: Path, names: Sequence[str]) -> list[Path]:
    """Files with one of these names, in the project and a few folders down, outside installed
    dependencies."""
    found: list[Path] = []

    def walk(folder: Path, depth: int) -> None:
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.is_file() and entry.name in names:
                found.append(entry)
            elif entry.is_dir() and depth < SEARCH_DEPTH and entry.name not in SKIP_DIRS:
                walk(entry, depth + 1)

    walk(root, 0)
    return found


def _has(root: Path, *names: str) -> bool:
    return bool(_files(root, names))


def _rel(root: Path, path: Path | str) -> str:
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path).replace("\\", "/")


def _tracked(root: Path) -> list[Path]:
    """The files git tracks, or every file outside installed dependencies when it is not a
    repository. A secret in a file git ignores has not been published."""
    try:
        listed = _run(["git", "ls-files", "-z"], root, ok=(0,))
        return [root / name for name in listed.split("\0") if name]
    except NotRun:
        return [
            p
            for p in root.rglob("*")
            if p.is_file() and not any(part in SKIP_DIRS for part in p.parts)
        ]


# Secrets -------------------------------------------------------------------------------------


def secrets(root: Path) -> list[Finding]:
    found = []
    for path in _tracked(root):
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        where = _rel(root, path)
        # A key in a test file is nearly always a made-up one the test needs.
        fixture = TEST_FILE.search(where) is not None
        for match in find_secrets(text):
            found.append(
                Finding(
                    "security",
                    f"secret-{match.kind}" + ("-in-tests" if fixture else ""),
                    "low" if fixture else "critical",
                    f"Committed secret: {match.kind.replace('_', ' ')}"
                    + (" (in a test file; check it is not a real one)" if fixture else ""),
                    f"{where}:{match.line}",
                    match.preview,
                    "Revoke it where it was issued, then remove it from the code and read it "
                    "from the environment. Removing the line does not un-publish the secret.",
                    "OWASP Top 10 A07 Identification and Authentication Failures",
                    "prat secrets",
                )
            )
    return found


def gitleaks(root: Path) -> list[Finding]:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "leaks.json"
        _run(
            ["gitleaks", "detect", "--source", ".", "--no-banner", "--redact",
             "--report-format", "json", "--report-path", str(out)],
            root,
        )  # fmt: skip
        data = json.loads(out.read_text(encoding="utf-8") or "[]") if out.is_file() else []
    return gitleaks_findings(data)


def gitleaks_findings(data: Sequence[Any]) -> list[Finding]:
    found = []
    for item in data:
        where = str(item.get("File") or "").replace("\\", "/")
        # As with PRAT's own scan: a key in a test file is nearly always a made-up one.
        fixture = TEST_FILE.search(where) is not None
        rule = str(item.get("RuleID") or "generic").lower()
        said = str(item.get("Description") or "A secret is committed").split(",")[0].rstrip(".")
        found.append(
            Finding(
                "security",
                f"secret-{rule}" + ("-in-tests" if fixture else ""),
                "low" if fixture else "critical",
                said + (" (in a test file; check it is not a real one)" if fixture else ""),
                f"{where}:{item.get('StartLine') or 0}",
                f"commit {str(item.get('Commit') or '')[:12]}" if item.get("Commit") else "",
                "Revoke it where it was issued, then remove it from the code and its history.",
                "OWASP Top 10 A07 Identification and Authentication Failures",
                "gitleaks",
            )
        )
    return found


# Dependencies with known vulnerabilities -----------------------------------------------------

DEPENDENCY = "OWASP Top 10 A06 Vulnerable and Outdated Components"
NPM_SEVERITY = {"critical": "critical", "high": "high", "moderate": "medium", "low": "low"}


def npm_audit(root: Path) -> list[Finding]:
    found = []
    for lock in _files(root, ("package-lock.json",)):
        data = _json(_run(["npm", "audit", "--json"], lock.parent), "npm audit")
        for name, item in (data.get("vulnerabilities") or {}).items():
            advisories = [v for v in item.get("via") or [] if isinstance(v, dict)]
            said = "; ".join(str(v.get("title") or "") for v in advisories[:3])
            fix = item.get("fixAvailable")
            how = (
                f"Upgrade to {fix.get('name')} {fix.get('version')}."
                if isinstance(fix, dict)
                else "Run npm audit fix."
                if fix
                else "No fixed version is published; replace or isolate the package."
            )
            found.append(
                Finding(
                    "security",
                    f"dependency-{name}",
                    NPM_SEVERITY.get(str(item.get("severity")), "info"),
                    f"{name} has a known vulnerability",
                    _rel(root, lock),
                    f"{item.get('range') or ''} {said or 'through a dependency'}".strip(),
                    how,
                    DEPENDENCY,
                    "npm audit",
                )
            )
    return found


def pnpm_audit(root: Path) -> list[Finding]:
    found = []
    for lock in _files(root, ("pnpm-lock.yaml",)):
        data = _json(_run(["pnpm", "audit", "--json"], lock.parent), "pnpm audit")
        for item in (data.get("advisories") or {}).values():
            name = str(item.get("module_name") or "package")
            found.append(
                Finding(
                    "security",
                    f"dependency-{name}",
                    NPM_SEVERITY.get(str(item.get("severity")), "info"),
                    f"{name} has a known vulnerability",
                    _rel(root, lock),
                    f"{item.get('vulnerable_versions') or ''} {item.get('title') or ''}".strip(),
                    f"Upgrade to {item.get('patched_versions') or 'a fixed version'}.",
                    DEPENDENCY,
                    "pnpm audit",
                )
            )
    return found


def pip_audit(root: Path) -> list[Finding]:
    if util.find_spec("pip_audit") is None:
        raise NotRun("pip-audit is not installed")
    found = []
    targets = _files(root, ("requirements.txt",)) or _files(root, ("pyproject.toml",))[:1]
    for target in targets:
        args = ["-r", target.name] if target.name == "requirements.txt" else ["."]
        text = _run(
            [sys.executable, "-m", "pip_audit", "-f", "json", "--progress-spinner", "off", *args],
            target.parent,
        )
        for package in _json(text, "pip-audit").get("dependencies") or []:
            vulns = package.get("vulns") or []
            if not vulns:
                continue
            # One finding for the package: the upgrade that clears every advisory is one change.
            names = list(dict.fromkeys(str(v.get("id")) for v in vulns))
            fixed = _highest([str(f) for v in vulns for f in v.get("fix_versions") or []])
            first = " ".join(str(vulns[0].get("description") or "").split())[:200]
            found.append(
                Finding(
                    "security",
                    f"dependency-{package.get('name')}",
                    # pip-audit names the advisories and gives no rating of its own.
                    "high",
                    f"{package.get('name')} has a known vulnerability",
                    _rel(root, target),
                    f"{package.get('version')}: {len(names)} advisories "
                    f"({', '.join(names[:6])}). {first}".strip(),
                    f"Upgrade to {fixed} or later." if fixed else "No fixed version is published.",
                    DEPENDENCY,
                    "pip-audit",
                )
            )
    return found


def _highest(versions: Sequence[str]) -> str:
    """The highest of some version numbers, compared part by part."""

    def parts(version: str) -> tuple[int, ...]:
        return tuple(int(n) for n in re.findall(r"\d+", version)[:4])

    return max(versions, key=parts, default="")


def composer_audit(root: Path) -> list[Finding]:
    found = []
    for lock in _files(root, ("composer.lock",)):
        text = _run(["composer", "audit", "--format=json", "--no-interaction"], lock.parent)
        advisories = _json(text, "composer audit").get("advisories") or {}
        packages = advisories.items() if isinstance(advisories, dict) else []
        for name, items in packages:
            for item in items if isinstance(items, list) else list(items.values()):
                found.append(
                    Finding(
                        "security",
                        f"dependency-{name}",
                        str(item.get("severity") or "high").lower(),
                        f"{name} has a known vulnerability",
                        _rel(root, lock),
                        f"{item.get('affectedVersions') or ''} {item.get('title') or ''} "
                        f"{item.get('cve') or ''}".strip(),
                        "Upgrade past the affected versions.",
                        DEPENDENCY,
                        "composer audit",
                    )
                )
    return found


# Code that is unsafe as written --------------------------------------------------------------

CODE = "OWASP Top 10 A03 Injection and related weaknesses"
BANDIT_SEVERITY = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
SEMGREP_SEVERITY = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}


def bandit(root: Path) -> list[Finding]:
    if util.find_spec("bandit") is None:
        raise NotRun("bandit is not installed")
    skip = ",".join(f"*/{name}/*" for name in SKIP_DIRS)
    # B101 is the use of assert, which is how tests are written.
    text = _run(
        [sys.executable, "-m", "bandit", "-r", ".", "-f", "json", "-q", "-x", skip, "-s", "B101"],
        root,
    )
    found = []
    for item in _json(text, "bandit").get("results") or []:
        where = str(item.get("filename") or "").replace("\\", "/").removeprefix("./")
        # Test code is not what runs for a visitor, and a low rating the tool itself is unsure
        # of is noise in a first report.
        unsure = item.get("issue_severity") == "LOW" and item.get("issue_confidence") != "HIGH"
        if TEST_FILE.search(where) or unsure:
            continue
        said = str(item.get("issue_text") or "")
        name = str(item.get("test_name") or item.get("test_id") or "unsafe code")
        # Bandit files a family of calls under one name; what it says of each is the title.
        title = said.split(".")[0][:90] if name == "blacklist" else name.replace("_", " ")
        found.append(
            Finding(
                "security",
                f"code-{item.get('test_id')}",
                BANDIT_SEVERITY.get(str(item.get("issue_severity")), "low"),
                title,
                f"{where}:{item.get('line_number')}",
                said,
                str(item.get("more_info") or ""),
                CODE,
                "bandit",
            )
        )
    return found


def semgrep(root: Path) -> list[Finding]:
    text = _run(["semgrep", "scan", "--config", "auto", "--json", "--quiet"], root)
    found = []
    for item in _json(text, "semgrep").get("results") or []:
        extra = item.get("extra") or {}
        rule = str(item.get("check_id") or "rule").rsplit(".", 1)[-1]
        found.append(
            Finding(
                "security",
                f"code-{rule}",
                SEMGREP_SEVERITY.get(str(extra.get("severity")), "low"),
                rule.replace("-", " "),
                f"{item.get('path')}:{(item.get('start') or {}).get('line')}",
                str(extra.get("message") or "")[:300],
                str((extra.get("metadata") or {}).get("source") or ""),
                CODE,
                "semgrep",
            )
        )
    return found


def _on_path(program: str) -> Callable[[Path], bool]:
    return lambda root: shutil.which(program) is not None


def _python(root: Path) -> bool:
    return _has(root, "pyproject.toml", "requirements.txt", "setup.py")


SECURITY: tuple[Tool, ...] = (
    Tool(
        "gitleaks",
        "secrets committed to the repository and its history",
        "https://github.com/gitleaks/gitleaks#installing",
        _on_path("gitleaks"),
        gitleaks,
    ),
    Tool(
        "prat secrets",
        "secrets in tracked files (used when gitleaks is not installed)",
        "",
        lambda root: shutil.which("gitleaks") is None,
        secrets,
    ),
    Tool(
        "npm audit",
        "npm packages with known vulnerabilities",
        "Install Node.js",
        lambda root: _has(root, "package-lock.json"),
        npm_audit,
    ),
    Tool(
        "pnpm audit",
        "pnpm packages with known vulnerabilities",
        "npm install -g pnpm",
        lambda root: _has(root, "pnpm-lock.yaml"),
        pnpm_audit,
    ),
    Tool(
        "pip-audit",
        "Python packages with known vulnerabilities",
        "pip install 'prat[evaluate]'",
        _python,
        pip_audit,
    ),
    Tool(
        "composer audit",
        "PHP packages with known vulnerabilities",
        "Install Composer 2.4 or later",
        lambda root: _has(root, "composer.lock"),
        composer_audit,
    ),
    Tool(
        "bandit",
        "Python code that is unsafe as written",
        "pip install 'prat[evaluate]'",
        _python,
        bandit,
    ),
    Tool(
        "semgrep",
        "code that is unsafe as written, in most languages",
        "https://semgrep.dev/docs/getting-started (not available on Windows)",
        lambda root: True,
        semgrep,
    ),
)
