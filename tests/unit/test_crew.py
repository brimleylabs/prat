"""The crew: picking the lead, the skill loadout as a plugin, the helpers, the skill library."""

from __future__ import annotations

import json
from pathlib import Path

from prat import crew
from prat.runtime.base import TOOL_BASH


def test_the_lead_follows_the_build_files(tmp_path: Path) -> None:
    (tmp_path / "artisan").write_text("")
    assert crew.detect_stack(tmp_path) == "laravel"
    other = tmp_path / "web"
    other.mkdir()
    (other / "package.json").write_text('{"dependencies": {"react": "18"}}')
    assert crew.detect_stack(other) == "react"
    assert crew.lead_for({"lead": "Gage"}, tmp_path).key == "go"
    assert crew.lead_for(None, tmp_path).name == "Lark"


def test_the_loadout_carries_core_stack_and_project_skills(tmp_path: Path) -> None:
    own = crew.project_skills(tmp_path, "shop") / "shop-vocabulary"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text(
        "---\nname: shop-vocabulary\ndescription: x\nphase: plan\n---\nbody\n"
    )
    member = crew.MEMBERS["laravel"]
    skills = crew.loadout(member, {}, tmp_path, "shop")
    assert set(crew.CORE_SKILLS) <= set(skills)
    assert "laravel-development" in skills and "shop-vocabulary" in skills
    root = crew.build_loadout(tmp_path, "shop", skills)
    manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == crew.PLUGIN_NAME
    assert (root / "skills" / "shop-vocabulary" / "SKILL.md").is_file()
    phases = crew.skills_by_phase(tmp_path, "shop", skills)
    assert "shop-vocabulary" in phases["plan"]
    named = crew.loadout(member, {"skills": ["go-development"]}, tmp_path, "shop")
    assert "go-development" in named and "laravel-development" not in named


def test_helpers_carry_their_own_memory_and_only_their_tools() -> None:
    helpers = crew.helpers(["scout", "tester"], {"scout": "# Your memory\n- routes live in api/"})
    assert set(helpers) == {"scout", "tester"}
    assert "routes live in api/" in helpers["scout"].prompt
    assert TOOL_BASH not in helpers["scout"].tools and TOOL_BASH in helpers["tester"].tools


def test_every_shipped_skill_is_well_formed() -> None:
    library = crew.library()
    names = sorted(p.name for p in library.iterdir() if p.is_dir())
    assert set(crew.CORE_SKILLS) <= set(names)
    for name in names:
        text = (library / name / "SKILL.md").read_text(encoding="utf-8")
        meta = crew.skill_meta(library / name / "SKILL.md")
        assert meta.get("name") == name, name
        assert meta.get("description") and meta.get("phase") in (
            "plan",
            "build",
            "verify",
            "any",
        ), name
        assert "—" not in text, f"{name} has an em dash"
