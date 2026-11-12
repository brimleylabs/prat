"""Suggest a starter ``.prat/project.toml`` by looking at a checkout.

Reads a few well-known files (``pyproject.toml``, ``package.json``, ``go.mod``
and so on) and proposes commands and gates for the stack it recognises. It
only reads; the person reviews the suggestion and commits it themselves.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prat.git import Git

# Lockfile to package manager, most specific first.
NODE_LOCKFILES: tuple[tuple[str, str], ...] = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lockb", "bun"),
    ("bun.lock", "bun"),
    ("package-lock.json", "npm"),
)
NODE_INSTALL = {
    "npm": "npm ci",
    "pnpm": "pnpm install",
    "yarn": "yarn install",
    "bun": "bun install",
}


@dataclass
class Suggestion:
    name: str
    default_branch: str
    stacks: list[str] = field(default_factory=list)
    commands: dict[str, str] = field(default_factory=dict)
    gates: list[str] = field(default_factory=list)
    protected_paths: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def recognised(self) -> bool:
        return bool(self.stacks)

    def toml(self) -> str:
        lines = [
            f"name = {_quote(self.name)}",
            f"default_branch = {_quote(self.default_branch)}",
            'autonomy = "low"',
        ]
        if self.protected_paths:
            items = ", ".join(_quote(p) for p in self.protected_paths)
            lines.append(f"protected_paths = [{items}]")
        lines.append("")
        lines.append("[commands]")
        if self.commands:
            lines.extend(f"{name} = {_quote(cmd)}" for name, cmd in self.commands.items())
        else:
            lines.append('# test = "make test"')
        lines.append("")
        lines.append("[verify]")
        lines.append(f"gates = [{', '.join(_quote(g) for g in self.gates)}]")
        return "\n".join(lines) + "\n"


def _quote(value: str) -> str:
    # A JSON string of printable text is a valid TOML basic string.
    return json.dumps(value, ensure_ascii=False)


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _default_branch(checkout: Path) -> str:
    git = Git(checkout)
    remote = git.run("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False)
    if remote.ok and remote.stdout.strip().startswith("origin/"):
        return remote.stdout.strip().removeprefix("origin/")
    current = git.run("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return current.stdout.strip() if current.ok and current.stdout.strip() else "main"


def _python(checkout: Path, out: Suggestion) -> None:
    pyproject = checkout / "pyproject.toml"
    if not (pyproject.is_file() or (checkout / "setup.py").is_file()):
        return
    out.stacks.append("Python")
    text = json.dumps(_read_toml(pyproject)).lower()
    if (checkout / "uv.lock").is_file():
        out.commands["setup"] = "uv sync"
        run = "uv run "
    elif (checkout / "poetry.lock").is_file():
        out.commands["setup"] = "poetry install"
        run = "poetry run "
    else:
        run = "python -m "
        out.notes.append(
            "Gates run with the python on PATH. Add a setup command if they need the "
            "project's dependencies installed first."
        )
    if "pytest" in text or (checkout / "tests").is_dir() or (checkout / "pytest.ini").is_file():
        out.commands["test"] = f"{run}pytest -q"
    if "ruff" in text or (checkout / "ruff.toml").is_file():
        out.commands["lint"] = f"{run}ruff check ."


def _node(checkout: Path, out: Suggestion) -> None:
    package = checkout / "package.json"
    if not package.is_file():
        return
    out.stacks.append("Node")
    manager = next((pm for lock, pm in NODE_LOCKFILES if (checkout / lock).is_file()), "npm")
    if manager == "npm" and not (checkout / "package-lock.json").is_file():
        out.commands["setup"] = "npm install"
    else:
        out.commands["setup"] = NODE_INSTALL[manager]
    scripts = _read_json(package).get("scripts") or {}
    for name in ("test", "lint", "build"):
        script = scripts.get(name) if isinstance(scripts, dict) else None
        if isinstance(script, str) and "no test specified" not in script:
            out.commands.setdefault(name, f"{manager} run {name}")


def _go(checkout: Path, out: Suggestion) -> None:
    if not (checkout / "go.mod").is_file():
        return
    out.stacks.append("Go")
    out.commands.setdefault("build", "go build ./...")
    out.commands.setdefault("test", "go test ./...")
    out.commands.setdefault("lint", "go vet ./...")


def _rust(checkout: Path, out: Suggestion) -> None:
    if not (checkout / "Cargo.toml").is_file():
        return
    out.stacks.append("Rust")
    out.commands.setdefault("build", "cargo build")
    out.commands.setdefault("test", "cargo test")
    out.commands.setdefault("lint", "cargo clippy -- -D warnings")


def _php(checkout: Path, out: Suggestion) -> None:
    composer = checkout / "composer.json"
    if not composer.is_file():
        return
    out.stacks.append("PHP")
    out.commands.setdefault("setup", "composer install")
    data = _read_json(composer)
    scripts = data.get("scripts") or {}
    requires = {**(data.get("require-dev") or {}), **(data.get("require") or {})}
    if isinstance(scripts, dict) and "test" in scripts:
        out.commands.setdefault("test", "composer test")
    elif (checkout / "artisan").is_file():
        out.commands.setdefault("test", "php artisan test")
    elif "phpunit/phpunit" in requires or (checkout / "phpunit.xml").is_file():
        out.commands.setdefault("test", "vendor/bin/phpunit")
    if (checkout / "database" / "migrations").is_dir():
        out.protected_paths.append("database/migrations/**")


def _ruby(checkout: Path, out: Suggestion) -> None:
    if not (checkout / "Gemfile").is_file():
        return
    out.stacks.append("Ruby")
    out.commands.setdefault("setup", "bundle install")
    if (checkout / "spec").is_dir():
        out.commands.setdefault("test", "bundle exec rspec")
    elif (checkout / "Rakefile").is_file():
        out.commands.setdefault("test", "bundle exec rake test")


DETECTORS = (_python, _node, _go, _rust, _php, _ruby)
GATE_ORDER = ("build", "lint", "test")


def suggest(checkout: Path) -> Suggestion:
    """A starter project file for ``checkout``. Reads files and git refs only."""
    out = Suggestion(name=checkout.name, default_branch=_default_branch(checkout))
    for detect in DETECTORS:
        detect(checkout, out)
    out.gates = [name for name in GATE_ORDER if name in out.commands]
    if not out.recognised:
        out.notes.append(
            "No familiar build files found. Add the commands that build and test this "
            "repository, then list them as gates."
        )
    elif not out.gates:
        out.notes.append("No test command found. Without gates, nothing is checked before review.")
    return out
