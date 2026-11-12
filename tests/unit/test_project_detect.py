"""Starter project files suggested from what a checkout contains."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from prat.project_detect import Suggestion, suggest
from prat.projects import parse_spec


def write(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def package_json(**scripts: str) -> str:
    return json.dumps({"name": "web", "scripts": scripts})


@pytest.mark.parametrize(
    ("files", "stacks", "commands", "gates"),
    [
        (
            {
                "pyproject.toml": '[project]\nname = "x"\n[tool.ruff]\n',
                "uv.lock": "",
                "tests/a": "",
            },
            ["Python"],
            {"setup": "uv sync", "test": "uv run pytest -q", "lint": "uv run ruff check ."},
            ["lint", "test"],
        ),
        (
            {"setup.py": "", "tests/test_a.py": ""},
            ["Python"],
            {"test": "python -m pytest -q"},
            ["test"],
        ),
        (
            {"package.json": package_json(test="vitest", lint="eslint ."), "pnpm-lock.yaml": ""},
            ["Node"],
            {"setup": "pnpm install", "test": "pnpm run test", "lint": "pnpm run lint"},
            ["lint", "test"],
        ),
        (
            {"package.json": package_json(test='echo "Error: no test specified" && exit 1')},
            ["Node"],
            {"setup": "npm install"},
            [],
        ),
        (
            {"package.json": package_json(build="vite build"), "package-lock.json": "{}"},
            ["Node"],
            {"setup": "npm ci", "build": "npm run build"},
            ["build"],
        ),
        (
            {"go.mod": "module x\n"},
            ["Go"],
            {"build": "go build ./...", "test": "go test ./...", "lint": "go vet ./..."},
            ["build", "lint", "test"],
        ),
        (
            {"Cargo.toml": "[package]\n"},
            ["Rust"],
            {"build": "cargo build", "test": "cargo test", "lint": "cargo clippy -- -D warnings"},
            ["build", "lint", "test"],
        ),
        (
            {"composer.json": "{}", "artisan": "", "database/migrations/x.php": ""},
            ["PHP"],
            {"setup": "composer install", "test": "php artisan test"},
            ["test"],
        ),
        (
            {"Gemfile": "", "spec/a_spec.rb": ""},
            ["Ruby"],
            {"setup": "bundle install", "test": "bundle exec rspec"},
            ["test"],
        ),
    ],
)
def test_stacks(
    tmp_path: Path,
    files: dict[str, str],
    stacks: list[str],
    commands: dict[str, str],
    gates: list[str],
) -> None:
    found = suggest(write(tmp_path / "repo", files))
    assert found.stacks == stacks
    assert found.commands == commands
    assert found.gates == gates
    spec = parse_spec(found.toml())
    assert spec.name == "repo"
    assert list(spec.verify.gates) == gates
    assert set(spec.commands.defined()) == set(commands)


def test_mixed_repository_keeps_the_first_setup(tmp_path: Path) -> None:
    found = suggest(
        write(tmp_path / "app", {"composer.json": "{}", "package.json": package_json(build="x")})
    )
    assert found.stacks == ["Node", "PHP"]
    assert found.commands["setup"] == "npm install"
    assert found.gates == ["build"]
    assert parse_spec(found.toml()).protected_paths == []


def test_unknown_repository_still_parses(tmp_path: Path) -> None:
    found = suggest(write(tmp_path / "plain", {"README.md": "hi"}))
    assert not found.recognised
    assert "No familiar build files" in found.notes[0]
    spec = parse_spec(found.toml())
    assert spec.verify.gates == [] and spec.default_branch == "main"


def test_default_branch_comes_from_the_checkout(repo_factory: Callable[..., Path]) -> None:
    found = suggest(repo_factory("branchy"))
    assert found.default_branch == "main"
    assert found.name == "branchy"


def test_quotes_are_escaped() -> None:
    odd = Suggestion(name='we"ird\name', default_branch="main")
    assert parse_spec(odd.toml()).name == 'we"ird\name'
