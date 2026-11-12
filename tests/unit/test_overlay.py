"""The overlay: an installation's own rules, guidance and settings, which add and never loosen."""

from __future__ import annotations

from pathlib import Path

from prat.domain import Autonomy
from prat.overlay import (
    OverlayGuidedRuntime,
    load_overlay,
    overlay_path,
)
from prat.policy import Policy, ToolCall
from prat.projects import ProjectSpec
from prat.runtime.base import AgentTask
from prat.runtime.fake import FakeRuntime

RULES = """
require_person = ["schema"]

[[deny_command]]
pattern = "make deploy*"
reason = "deploys go through the platform team"

[[deny_path]]
pattern = "infra/**"
reason = "owned by the platform team"

[[deny_path]]
pattern = "vault/*.json"
reason = "credentials exports"
read = true

[[change_rule]]
paths = ["billing/**"]
category = "billing"
risk = "high"

[limits]
max_diff_lines = 400
max_diff_files = 500
"""


def write(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def full_overlay(home: Path) -> Path:
    return write(
        overlay_path(home),
        {
            "overlay.toml": 'overlay_api = 1\nname = "acme"\n',
            "rules/platform.toml": RULES,
            "prompts/builder.md": "Run `make lint` before finishing.",
            "prompts/critic.md": "Check that migrations have a down step.",
            "settings.toml": '[models]\ncritic = "claude-opus-5-5"\n',
        },
    )


def test_no_overlay_changes_nothing(tmp_path: Path) -> None:
    found = load_overlay(overlay_path(tmp_path))
    assert not found.present and not found.active
    spec = ProjectSpec(name="app")
    assert found.apply_to_spec(spec) == spec
    assert found.guidance_for("builder") is None


def test_a_full_overlay_applies(tmp_path: Path) -> None:
    found = load_overlay(full_overlay(tmp_path))
    assert found.disabled == []
    assert sorted(found.loaded) == sorted(
        [
            "overlay.toml",
            "rules/platform.toml",
            "prompts/builder.md",
            "prompts/critic.md",
            "settings.toml",
        ]
    )
    spec = found.apply_to_spec(
        ProjectSpec(name="app", protected_paths=["migrations/**"], max_diff_lines=1000)
    )
    assert spec.protected_paths == ["migrations/**", "infra/**", "vault/*.json"]
    assert spec.secret_paths == ["vault/*.json"]
    assert spec.changes.require_person == ["schema"]
    assert [r.category for r in spec.changes.rule] == ["billing"]
    assert spec.max_diff_lines == 400
    # A higher limit than the project's has no effect: the overlay only lowers.
    assert spec.max_diff_files == ProjectSpec(name="app").max_diff_files
    assert found.models_under({}) == {"critic": "claude-opus-5-5"}
    assert found.models_under({"critic": "claude-sonnet-5-5"}) == {"critic": "claude-sonnet-5-5"}


def test_the_policy_denies_overlay_commands_even_behind_wrappers(tmp_path: Path) -> None:
    found = load_overlay(full_overlay(tmp_path))
    work = tmp_path / "work"
    work.mkdir()
    policy = Policy(Autonomy.HIGH, work, denied_commands=found.policy_denied_commands())
    for command in ("make deploy", "make deploy ENV=prod", "env CI=1 make deploy"):
        decision = policy(ToolCall.bash(command))
        assert not decision.allowed, command
        assert "platform team" in decision.reason
    assert policy(ToolCall.bash("make test")).allowed
    plain = Policy(Autonomy.HIGH, work)
    assert plain(ToolCall.bash("make deploy")).allowed


def test_guidance_reaches_the_right_role(tmp_path: Path) -> None:
    full_overlay(tmp_path)
    runtime = OverlayGuidedRuntime(FakeRuntime([]), tmp_path)
    assert runtime.name == "fake"

    def task(role: str) -> AgentTask:
        return AgentTask(
            prompt="go",
            workdir=tmp_path,
            allowed_tools=frozenset(),
            policy=lambda call: None,  # type: ignore[arg-type,return-value]
            system_prompt="PRAT rules",
            metadata={"role": role},
        )

    builder = runtime._guided(task("builder")).system_prompt
    assert builder.startswith("PRAT rules")
    assert "Installation guidance" in builder and "make lint" in builder
    assert "the rules above win" in builder
    assert "down step" in runtime._guided(task("critic")).system_prompt
    assert runtime._guided(task("planner")).system_prompt == "PRAT rules"


def test_nothing_in_an_overlay_can_loosen(tmp_path: Path) -> None:
    root = write(
        overlay_path(tmp_path),
        {
            "overlay.toml": "overlay_api = 1\n",
            "rules/good.toml": '[[deny_command]]\npattern = "npm publish*"\nreason = "no"\n',
            "rules/allow.toml": '[[allow_command]]\npattern = "git push*"\nreason = "yes"\n',
            "rules/escape.toml": '[[deny_path]]\npattern = "../../etc/*"\nreason = "x"\n',
            "rules/absolute.toml": '[[deny_path]]\npattern = "/etc/passwd"\nreason = "x"\n',
            "rules/broken.toml": "deny_command = [",
            "rules/notes.txt": "x",
            "prompts/janitor.md": "tidy up",
            "prompts/builder.md": "x" * 7000,
            "settings.toml": "[models]\nwizard = 'claude-opus-5-5'\n",
            "checks/review.toml": "",
            "random.txt": "",
        },
    )
    found = load_overlay(root)
    off = {issue.item: issue.reason for issue in found.disabled}
    assert "allow_command" in off["rules/allow.toml"]
    assert "relative" in off["rules/escape.toml"]
    assert "relative" in off["rules/absolute.toml"]
    assert "rules/broken.toml" in off
    assert "rules/notes.txt" in off
    assert "prompts/janitor.md" in off
    assert "longer than" in off["prompts/builder.md"]
    assert "settings.toml" in off
    assert off["checks/"] == "not read by this prat version yet"
    assert "random.txt" in off
    # The valid file still applies.
    assert [d.pattern for d in found.denied_commands] == ["npm publish*"]
    assert found.loaded == ["overlay.toml", "rules/good.toml"]


def test_a_newer_overlay_api_switches_everything_off(tmp_path: Path) -> None:
    root = write(
        overlay_path(tmp_path),
        {"overlay.toml": "overlay_api = 2\n", "rules/a.toml": RULES},
    )
    found = load_overlay(root)
    assert not found.active and found.api == 2
    assert "not one this PRAT reads" in found.disabled[0].reason
    assert found.denied_commands == []


def test_a_missing_manifest_switches_everything_off(tmp_path: Path) -> None:
    root = write(overlay_path(tmp_path), {"rules/a.toml": RULES})
    found = load_overlay(root)
    assert found.present and not found.active
    assert found.disabled[0].item == "overlay.toml"
