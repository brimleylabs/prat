from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from prat.domain import Autonomy
from prat.paths import ProtectedPathError
from prat.projects import (
    EXAMPLE_PROJECT_TOML,
    PROJECT_FILE_REL,
    ProjectFileError,
    ProjectSpec,
    check_project_paths,
    command_argv,
    command_text,
    default_agent_path,
    load_spec,
    parse_spec,
    slugify,
    validate_slug,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_example_file_is_valid() -> None:
    spec = parse_spec(EXAMPLE_PROJECT_TOML)
    assert spec.name == "acme-api"
    assert spec.autonomy is Autonomy.LOW
    assert spec.verify.gates == ["test", "lint"]
    assert spec.commands.defined().keys() == {"setup", "test", "lint"}
    assert ".git" in spec.all_protected
    assert ".prat/" in spec.all_protected
    assert ".env" in spec.all_secret_paths


def test_minimal_file_uses_defaults() -> None:
    spec = parse_spec('name = "acme-api"\n')
    assert spec.default_branch == "main"
    assert spec.autonomy is Autonomy.LOW
    assert spec.verify.gates == []


def test_gates_must_reference_defined_commands() -> None:
    with pytest.raises(ProjectFileError, match="not defined"):
        parse_spec('name = "x"\n[verify]\ngates = ["test"]\n')


def test_unknown_keys_rejected() -> None:
    with pytest.raises(ProjectFileError):
        parse_spec('name = "x"\nautopush = true\n')


def test_unknown_gate_name_rejected() -> None:
    with pytest.raises(ProjectFileError):
        parse_spec('name = "x"\n[commands]\ntest = "t"\n[verify]\ngates = ["deploy"]\n')


def test_invalid_toml_reported() -> None:
    with pytest.raises(ProjectFileError, match="invalid TOML"):
        parse_spec("name = ")


@pytest.mark.parametrize("branch", ["-main", "a..b", "main.lock", "has space", "x/"])
def test_bad_branch_names(branch: str) -> None:
    with pytest.raises(ProjectFileError):
        parse_spec(f'name = "x"\ndefault_branch = "{branch}"\n')


def test_patterns_cannot_escape_repo() -> None:
    with pytest.raises(ProjectFileError, match="escapes"):
        parse_spec('name = "x"\nprotected_paths = ["../outside"]\n')


def test_empty_command_rejected() -> None:
    with pytest.raises(ProjectFileError):
        parse_spec('name = "x"\n[commands]\ntest = "  "\n')


def test_command_forms() -> None:
    assert command_argv('python -m pytest -q "tests/a b.py"') == [
        "python",
        "-m",
        "pytest",
        "-q",
        "tests/a b.py",
    ]
    assert command_argv(["npm", "test"]) == ["npm", "test"]
    spec = parse_spec('name = "x"\n[commands]\nlint = ["ruff", "check", "."]\n')
    assert spec.commands.lint == ["ruff", "check", "."]


def test_load_spec_without_file_uses_directory_name(tmp_path: Path) -> None:
    spec = load_spec(tmp_path / "acme-web")
    assert spec.name == "acme-web"
    assert isinstance(spec, ProjectSpec)


def test_load_spec_reads_file(tmp_path: Path) -> None:
    (tmp_path / ".prat").mkdir()
    (tmp_path / ".prat" / "project.toml").write_text('name = "acme-api"\nautonomy = "high"\n')
    assert load_spec(tmp_path).autonomy is Autonomy.HIGH


def test_slugs() -> None:
    assert slugify("Add a greet() function!") == "add-a-greet-function"
    assert slugify("***") == "item"
    assert len(slugify("x" * 100)) == 40
    assert validate_slug("acme-api") == "acme-api"
    with pytest.raises(ValueError, match="invalid slug"):
        validate_slug("Acme API")


def test_checkout_and_agent_paths_must_be_separate(tmp_path: Path) -> None:
    checkout = tmp_path / "acme-api"
    check_project_paths(checkout, tmp_path / "acme-api-agent")
    with pytest.raises(ProtectedPathError):
        check_project_paths(checkout, checkout / "agent")
    with pytest.raises(ProtectedPathError):
        check_project_paths(checkout, checkout)
    assert default_agent_path(checkout).name == "acme-api-agent"


def test_python_placeholder_is_the_running_interpreter() -> None:
    assert command_argv("{python} -m pytest -q") == [sys.executable, "-m", "pytest", "-q"]
    assert command_argv(["{python}", "-c", "print('{python}')"]) == [
        sys.executable,
        "-c",
        f"print('{sys.executable}')",
    ]
    # Substituted after splitting: a path with spaces or backslashes stays one argument.
    assert len(command_argv("{python} -V")) == 2


def test_command_text_splits_back_to_the_same_arguments() -> None:
    for command in ("{python} -m ruff check src tests", ["{python}", "-m", "mypy"]):
        text = command_text(command)
        assert "{python}" not in text
        again = shlex.split(text, posix=True)
        assert again[1:] == command_argv(command)[1:]
        assert Path(again[0]) == Path(sys.executable)
    assert command_text("npm test") == "npm test"


def test_format_gate() -> None:
    spec = parse_spec(
        'name = "x"\n[commands]\ntest = "t"\nformat = "f --check"\n'
        '[verify]\ngates = ["test", "format"]\n'
    )
    assert spec.verify.gates == ["test", "format"]
    assert list(spec.commands.defined()) == ["test", "format"]


def test_change_rules_are_validated() -> None:
    with pytest.raises(ProjectFileError, match="invalid category"):
        parse_spec('name = "x"\n[[changes.rule]]\npaths = ["a/**"]\ncategory = "Not Valid"\n')
    with pytest.raises(ProjectFileError, match="escapes"):
        parse_spec('name = "x"\n[[changes.rule]]\npaths = ["../a"]\ncategory = "security"\n')
    with pytest.raises(ProjectFileError):
        parse_spec('name = "x"\n[[changes.rule]]\npaths = []\ncategory = "security"\n')
    with pytest.raises(ProjectFileError):
        parse_spec(
            'name = "x"\n[[changes.rule]]\npaths = ["a"]\ncategory = "security"\nrisk = "none"\n'
        )
    with pytest.raises(ProjectFileError, match="require_person"):
        parse_spec('name = "x"\n[changes]\nrequire_person = ["a b"]\n')


def test_the_repository_carries_its_own_project_file() -> None:
    spec = load_spec(REPO_ROOT)
    assert (REPO_ROOT / PROJECT_FILE_REL).is_file()
    assert spec.name == "prat"
    # Test, lint and the type check (the build gate) are all required.
    assert {"test", "lint", "build"} <= set(spec.verify.gates)
    assert spec.changes.require_person == []
    for name in spec.verify.gates:
        argv = command_argv(spec.commands.defined()[name])
        assert argv[0] == sys.executable, name
    assert "mypy" in command_argv(spec.commands.defined()["build"])
    assert spec.notes.strip()
