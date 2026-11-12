from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from prat.config import load_settings
from prat.model_registry import (
    DEFAULT_MODELS,
    FALLBACK_PRICE,
    HAIKU,
    OPUS,
    SONNET,
    Role,
    estimate_cost,
    price_for,
    resolve_model,
    validate_role_map,
)
from prat.projects import ProjectFileError, parse_spec


def test_defaults() -> None:
    assert DEFAULT_MODELS[Role.BUILDER] == SONNET == "claude-sonnet-5-5"
    assert DEFAULT_MODELS[Role.PLANNER] == OPUS == "claude-opus-5-5"
    assert DEFAULT_MODELS[Role.CRITIC] == SONNET
    # The scout's first look runs once per project, so it gets the strongest model.
    assert DEFAULT_MODELS[Role.SCOUT] == OPUS
    for role in (Role.DRAFTER, Role.SUMMARIZER, Role.LIBRARIAN):
        assert DEFAULT_MODELS[role] == HAIKU == "claude-haiku-5-5"
    assert set(DEFAULT_MODELS) == set(Role)


def test_project_beats_settings_beats_default() -> None:
    settings = {"builder": HAIKU, "critic": HAIKU}
    project = {"builder": OPUS}
    assert resolve_model("builder", settings_models=settings, project_models=project) == OPUS
    assert resolve_model(Role.CRITIC, settings_models=settings, project_models=project) == HAIKU
    assert resolve_model(Role.PLANNER, settings_models=settings) == OPUS
    with pytest.raises(ValueError, match="not a valid Role"):
        resolve_model("janitor")


def test_role_maps_are_validated(tmp_path: Path) -> None:
    assert validate_role_map({"builder": " x "}) == {"builder": "x"}
    with pytest.raises(ValueError, match="unknown model role"):
        validate_role_map({"janitor": "x"})
    with pytest.raises(ValidationError):
        load_settings(tmp_path, models={"janitor": "x"})
    assert load_settings(tmp_path, models={"builder": HAIKU}).models == {"builder": HAIKU}
    spec = parse_spec('name = "x"\n[models]\ncritic = "claude-haiku-5-5"\n')
    assert spec.models == {"critic": HAIKU}
    with pytest.raises(ProjectFileError):
        parse_spec('name = "x"\n[models]\nbogus = "m"\n')


def test_settings_from_toml_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "config.toml").write_text(
        'auth_mode = "claude_login"\n[models]\nbuilder = "claude-haiku-5-5"\n', encoding="utf-8"
    )
    settings = load_settings(tmp_path)
    assert str(settings.auth_mode) == "claude_login"
    assert settings.models == {"builder": HAIKU}
    monkeypatch.setenv("PRAT_MODELS", '{"critic": "claude-opus-5-5"}')
    # The environment adds to (and overrides keys of) the file's table.
    assert load_settings(tmp_path).models == {"builder": HAIKU, "critic": OPUS}


def test_cost_estimates() -> None:
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_read_input_tokens": 1_000_000,
        "cache_creation_input_tokens": 0,
    }
    assert estimate_cost(SONNET, usage) == pytest.approx(2.0 + 10.0 + 0.2)
    assert estimate_cost(HAIKU, {"output_tokens": 2_000_000}) == pytest.approx(1.0)
    assert estimate_cost(SONNET, None) == 0.0
    assert estimate_cost(SONNET, {"input_tokens": "lots"}) == 0.0
    assert price_for("some-future-model") == FALLBACK_PRICE  # unknown is never free
    assert price_for(None) == FALLBACK_PRICE


def test_loop_settings_validated(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="loop_window"):
        load_settings(tmp_path, loop_threshold=6, loop_window=5)
