from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_dir_link
from prat import paths


@pytest.mark.parametrize(
    ("rel", "pattern", "expected"),
    [
        ("migrations/001.sql", "migrations/**", True),
        ("migrations", "migrations/**", True),
        ("src/migrations/001.sql", "migrations/**", False),
        ("poetry.lock", "*.lock", True),
        ("sub/dir/yarn.lock", "*.lock", True),
        ("lockfile", "*.lock", False),
        (".git", ".git", True),
        (".git/config", ".git", True),
        (".prat/project.toml", ".prat/", True),
        ("a/.prat/x", ".prat/", True),
        ("docs/a/b.md", "docs/**/*.md", True),
        ("docs/b.md", "docs/**/*.md", True),
        ("src/app.py", "src/*.py", True),
        ("src/pkg/app.py", "src/*.py", False),
        ("build/out.js", "build", True),
    ],
)
def test_glob_match(rel: str, pattern: str, expected: bool) -> None:
    assert paths.glob_match(rel, pattern) is expected


def test_glob_case_sensitivity() -> None:
    assert paths.glob_match("Migrations/1.sql", "migrations/**", case_insensitive=True)
    assert not paths.glob_match("Migrations/1.sql", "migrations/**", case_insensitive=False)


def test_matches_any_returns_pattern() -> None:
    assert paths.matches_any("x/.env", [".env", "*.pem"]) == ".env"
    assert paths.matches_any("x/app.py", [".env", "*.pem"]) is None


def test_is_within(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    assert paths.is_within(root / "a" / "file", root)
    assert paths.is_within(root, root)
    assert not paths.is_within(tmp_path / "rootless", root)
    assert not paths.is_within(tmp_path, root)
    assert paths.is_within(root / "a" / ".." / "a", root)
    assert not paths.is_within(root / ".." / "other", root)


def test_case_insensitive_comparison_follows_filesystem(tmp_path: Path) -> None:
    root = tmp_path / "Root"
    root.mkdir()
    upper = tmp_path / "ROOT" / "x"
    if paths.is_case_insensitive(root):
        assert paths.is_within(upper, root)
    else:
        assert not paths.is_within(upper, root)


def test_symlinks_are_resolved(tmp_path: Path) -> None:
    protected = tmp_path / "checkout"
    protected.mkdir()
    link = tmp_path / "innocent-link"
    if not make_dir_link(link, protected):
        pytest.skip("directory links are not available on this system")
    assert paths.is_within(link / "file.txt", protected)
    with pytest.raises(paths.ProtectedPathError):
        paths.assert_outside(link, [protected])


def test_assert_outside(tmp_path: Path) -> None:
    protected = tmp_path / "checkout"
    paths.assert_outside(tmp_path / "elsewhere", [protected])
    with pytest.raises(paths.ProtectedPathError):
        paths.assert_outside(protected / "sub", [protected])
    with pytest.raises(paths.ProtectedPathError):
        paths.assert_outside(tmp_path, [protected])  # a parent of a protected path


def test_relative_posix(tmp_path: Path) -> None:
    assert paths.relative_posix(tmp_path / "a" / "b.txt", tmp_path) == "a/b.txt"
    assert paths.relative_posix(tmp_path, tmp_path) == ""
    assert paths.relative_posix(tmp_path.parent, tmp_path) is None
