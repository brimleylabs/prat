from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from prat.config import load_settings, resolve_home
from prat.domain import Autonomy


def test_defaults(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "home")
    assert settings.home == (tmp_path / "home").resolve()
    assert settings.max_concurrent_runs == 3
    assert settings.max_concurrent_per_project == 1
    assert settings.max_autonomy is Autonomy.MEDIUM
    assert settings.db_url == f"sqlite:///{(settings.home / 'prat.db').as_posix()}"
    assert settings.worktrees_dir == settings.home / "worktrees"


def test_home_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAT_HOME", str(tmp_path / "elsewhere"))
    assert resolve_home() == (tmp_path / "elsewhere").resolve()
    assert load_settings().home == (tmp_path / "elsewhere").resolve()


def test_home_default_is_under_user_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRAT_HOME", raising=False)
    assert resolve_home() == (Path.home() / ".prat").resolve()


def test_toml_file_is_read(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        'max_concurrent_runs = 7\nmax_autonomy = "low"\n'
        'database_url = "postgresql+psycopg://PRAT@localhost/PRAT"\n',
        encoding="utf-8",
    )
    settings = load_settings(home)
    assert settings.max_concurrent_runs == 7
    assert settings.max_autonomy is Autonomy.LOW
    assert settings.db_url.startswith("postgresql")


def test_env_beats_toml_and_overrides_beat_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("max_concurrent_runs = 7\n", encoding="utf-8")
    monkeypatch.setenv("PRAT_MAX_CONCURRENT_RUNS", "5")
    assert load_settings(home).max_concurrent_runs == 5
    assert load_settings(home, max_concurrent_runs=2).max_concurrent_runs == 2


def test_invalid_values_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_settings(tmp_path, max_concurrent_runs=0)
    with pytest.raises(ValidationError):
        load_settings(tmp_path, max_autonomy="reckless")


def test_unknown_keys_in_toml_rejected(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("not_a_setting = 1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(tmp_path)


def test_ensure_dirs(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "home")
    settings.ensure_dirs()
    for path in (settings.home, settings.worktrees_dir, settings.journal_dir, settings.runs_dir):
        assert path.is_dir()
    assert settings.run_dir(4) == settings.runs_dir / "run-4"
