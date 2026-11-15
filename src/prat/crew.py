"""The crew: who leads a card, the helpers it can call, and the skills it carries.

PRAT keeps one agent in charge of a card from start to finish (a pipeline of agents handing
summaries to each other loses context and adds up their time). That lead is a **crew member**
with a stack, a name and a short persona; the knowledge is in its skills. Around it:

* **Helpers** it can call at any moment, several at once, each returning only its answer:
  scout, tester, pair, data analyst, web checker and a self-reviewer (SDK subagents). Every
  tool call a helper makes goes through PRAT's policy like the lead's.
* **Skills**: a loadout per project, built at the start of each run as a small plugin folder
  under ``$PRAT_HOME/skills/<project>/loadout``: PRAT's core skills, the stack skills the
  project file names (``[crew] skills``), and the project's own promoted skills.
* **Memory**: each crew member, helper and reviewer has its own (prat.memory).

The project file can name its lead and skills; without ``[crew]`` PRAT works the stack out from
the checkout.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from prat.runtime.base import (
    READ_TOOLS,
    TOOL_BASH,
    TOOL_MCP,
    HelperSpec,
)

PLUGIN_NAME = "prat-loadout"
CORE_SKILLS = (
    "verify-before-finishing",
    "safe-database-changes",
    "ask-when-blocked",
    "use-your-helpers",
    "report-progress",
)


@dataclass(frozen=True)
class Member:
    """A lead: a stack, a name and the few lines of taste that make it that stack's engineer."""

    key: str
    name: str
    stack: str
    color: str
    persona: str
    skills: tuple[str, ...]


MEMBERS: Mapping[str, Member] = {
    m.key: m
    for m in (
        Member(
            "python",
            "Pip",
            "Python",
            "#3776ab",
            "On the crew you are the Python engineer. You write typed, plain Python 3.12: "
            "pathlib, logging, dataclasses, small functions with clear names. You prefer the "
            "standard library and the project's existing patterns to new dependencies, and you "
            "prove a change with the project's own tests before you call it done.",
            ("python-development",),
        ),
        Member(
            "react",
            "Juno",
            "React and TypeScript",
            "#149eca",
            "On the crew you are the front-end engineer. You build React with strict "
            "TypeScript, keep state where it belongs, respect the design system already in the "
            "repository and check a page really renders before you say it does.",
            ("react-development", "node-typescript-development", "web-ui-checks"),
        ),
        Member(
            "node",
            "Nova",
            "Node and TypeScript",
            "#3c873a",
            "On the crew you are the Node engineer. You write strict TypeScript, keep the "
            "package manager and lockfile the repository already uses, and test with what the "
            "project already runs.",
            ("node-typescript-development",),
        ),
        Member(
            "laravel",
            "Lark",
            "Laravel and PHP",
            "#ff2d20",
            "On the crew you are the Laravel engineer. You follow Laravel's conventions "
            "(artisan, migrations, form requests, Eloquent used carefully), read configuration "
            "through config(), and never change the application's code to suit the "
            "environment it runs in.",
            ("laravel-development", "php-development", "web-ui-checks"),
        ),
        Member(
            "php",
            "Wren",
            "PHP and WordPress",
            "#777bb4",
            "On the crew you are the PHP engineer. You write PHP 8 with strict types, escape "
            "output and check capabilities and nonces in WordPress code, and never update a "
            "lockfile nobody asked you to.",
            ("php-development", "web-ui-checks"),
        ),
        Member(
            "go",
            "Gage",
            "Go",
            "#00add8",
            "On the crew you are the Go engineer. Standard library first, errors wrapped with "
            "context, table-driven tests, gofmt and go vet clean.",
            ("go-development",),
        ),
        Member(
            "generalist",
            "Sam",
            "any stack",
            "#9aa6bc",
            "On the crew you are the generalist. You learn a codebase's own conventions before "
            "changing it, keep changes small and prove each one with the project's commands.",
            (),
        ),
    )
}


HELPER_PROMPTS: Mapping[str, tuple[str, str, frozenset[str], str]] = {
    # name: (description, instructions, PRAT tools, model alias)
    "scout": (
        "Read-only codebase questions: where something lives, how a part works, what calls "
        "what. Returns a short answer with file paths. Use before editing unfamiliar code.",
        "You are the crew's scout. Answer the one question you are given by reading the "
        "repository: never write or change anything. Reply in 3 to 10 sentences, citing file "
        "paths and line numbers, and say plainly when you could not find something.",
        READ_TOOLS,
        "haiku",
    ),
    "tester": (
        "Runs a failing test or check and finds why it fails. Returns the cause and one "
        "concrete next step. Use when a test, lint or build fails and the cause is not obvious.",
        "You are the crew's tester. Run the command you are given, read its output and the "
        "code it points at, and reply with: what failed, the likely cause, and one concrete "
        "next step. Quote at most 20 lines of output. Do not fix the code yourself.",
        READ_TOOLS | {TOOL_BASH},
        "haiku",
    ),
    "pair": (
        "A second opinion when stuck: the same thing tried several times, or two approaches "
        "and no way to choose. Returns one or two concrete suggestions.",
        "You are the crew's pair. Read the brief and the code it names, then give one or two "
        "concrete suggestions in under 150 words: what to try and why. Do not write the "
        "implementation.",
        READ_TOOLS,
        "sonnet",
    ),
    "data-analyst": (
        "Answers questions about the data with read-only SQL on the card's own database "
        "branch: counts, shapes, what a migration changed. Use before and after data changes.",
        "You are the crew's data analyst. Answer the question with read-only queries through "
        "the query_database tool (the card's own branch of the project's database). Show the "
        "query and the few numbers that answer it. Never try to change data.",
        READ_TOOLS | {TOOL_MCP},
        "haiku",
    ),
    "web-checker": (
        "Checks pages of a running app or preview with HTTP requests: status codes, that the "
        "page's own CSS, scripts and images load, no error page, key text present.",
        "You are the crew's web checker. Fetch the URLs you are given with curl, follow the "
        "page's own stylesheets, scripts and images (same origin) and report: status codes, "
        "any asset that failed, any error page (stack traces, fatal errors, framework debug "
        "pages) and whether the text you were asked about is there. Change nothing.",
        READ_TOOLS | {TOOL_BASH},
        "haiku",
    ),
    "reviewer": (
        "Reviews the lead's own change against the card's acceptance criteria before the "
        "lead finishes. Returns what is missing or wrong, if anything.",
        "You are the crew's self-review. Read the card's acceptance criteria you are given and "
        "the change (git diff against the card's start), and reply with each criterion: met, "
        "partly met or not met, with the evidence. List anything that looks wrong. Do not "
        "change anything.",
        READ_TOOLS | {TOOL_BASH},
        "sonnet",
    ),
}


def helpers(
    enabled: Iterable[str] | None = None, memories: Mapping[str, str] | None = None
) -> dict[str, HelperSpec]:
    """The helpers a lead may call, each with its own memory added to its instructions."""
    names = list(enabled) if enabled is not None else list(HELPER_PROMPTS)
    found = {}
    for name in names:
        if name not in HELPER_PROMPTS:
            continue
        description, prompt, tools, model = HELPER_PROMPTS[name]
        memory = (memories or {}).get(name, "")
        found[name] = HelperSpec(
            description=description,
            prompt=prompt + (f"\n\n{memory}" if memory else ""),
            tools=frozenset(tools),
            model=model,
        )
    return found


# Choosing the lead ---------------------------------------------------------------------


def detect_stack(checkout: Path) -> str:
    """The member that fits a checkout, from its build files."""

    def has(name: str) -> bool:
        return (checkout / name).exists()

    composer = _read(checkout / "composer.json")
    package = _read(checkout / "package.json")
    if has("artisan") or "laravel/framework" in composer:
        return "laravel"
    if has("go.mod"):
        return "go"
    if has("pyproject.toml") or has("requirements.txt") or has("setup.py"):
        return "python"
    if '"react"' in package or '"next"' in package:
        return "react"
    if package:
        return "node"
    if composer or has("wp-config.php") or has("index.php"):
        return "php"
    return "generalist"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:200_000]
    except OSError:
        return ""


def lead_for(crew: Mapping[str, Any] | None, checkout: Path) -> Member:
    """The project's lead: ``[crew] lead`` (a stack key or a member's name), else detected."""
    wanted = str((crew or {}).get("lead") or "").strip().lower()
    for member in MEMBERS.values():
        if wanted in (member.key, member.name.lower()):
            return member
    return MEMBERS[detect_stack(checkout)]


# Skills ------------------------------------------------------------------------------


def library() -> Path:
    """PRAT's own skills, shipped in the package."""
    return Path(str(resources.files("prat") / "resources" / "skills"))


def project_skills(home: Path, slug: str) -> Path:
    """A project's own promoted skills (never in the repository)."""
    return home / "skills" / slug / "skills"


def candidates_dir(home: Path, slug: str) -> Path:
    return home / "skills" / slug / "candidates"


def skill_meta(path: Path) -> dict[str, str]:
    """The frontmatter of a SKILL.md as a flat dict."""
    text = _read(path)
    if not text.startswith("---"):
        return {}
    meta: dict[str, str] = {}
    for line in text.split("\n")[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip().strip("\"'")
    return meta


def available_skills(home: Path, slug: str) -> dict[str, Path]:
    """Every skill this project may load, by name: PRAT's library, then the project's own."""
    found: dict[str, Path] = {}
    for root in (library(), project_skills(home, slug)):
        if not root.is_dir():
            continue
        for skill in sorted(root.iterdir()):
            if (skill / "SKILL.md").is_file():
                found[skill.name] = skill
    return found


def loadout(member: Member, crew: Mapping[str, Any] | None, home: Path, slug: str) -> list[str]:
    """The skills a run carries: core, the lead's stack (or ``[crew] skills``), the
    project's own."""
    available = available_skills(home, slug)
    named = (crew or {}).get("skills")
    stack = list(named) if isinstance(named, list) else list(member.skills)
    own = sorted(p.name for p in project_skills(home, slug).glob("*") if p.is_dir())
    wanted = [*CORE_SKILLS, *stack, *own]
    return [s for s in dict.fromkeys(wanted) if s in available]


def build_loadout(home: Path, slug: str, skills: Iterable[str]) -> Path:
    """Write the loadout as a Claude Code plugin folder and return it."""
    available = available_skills(home, slug)
    root = home / "skills" / slug / "loadout"
    if root.exists():
        shutil.rmtree(root)
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": PLUGIN_NAME, "version": "1.0.0", "description": "PRAT loadout"}),
        encoding="utf-8",
    )
    for name in skills:
        source = available.get(name)
        if source is not None:
            shutil.copytree(source, root / "skills" / name)
    return root


def skills_by_phase(home: Path, slug: str, skills: Iterable[str]) -> dict[str, list[str]]:
    """The loadout's skills grouped by the step they usually matter in."""
    available = available_skills(home, slug)
    grouped: dict[str, list[str]] = {}
    for name in skills:
        path = available.get(name)
        phase = skill_meta(path / "SKILL.md").get("phase", "any") if path else "any"
        grouped.setdefault(phase, []).append(name)
    return grouped


def crew_prompt(
    member: Member,
    helpers: Iterable[str],
    phases: Mapping[str, list[str]],
    *,
    helper_names: Mapping[str, str] | None = None,
) -> str:
    """The lines that tell the lead what it carries: its stack's taste, its helpers (by the
    names they chose, called by their role), its skills by when they matter."""
    lines = [member.persona, ""]
    keys = list(helpers)
    if keys:
        named = ", ".join(
            f"{helper_names[k]} (the {k})" if helper_names and k in helper_names else k
            for k in keys
        )
        lines.append(
            f"Your helpers: {named}. Call one with the Agent tool and its role as the "
            "subagent type, several at once when the questions are independent. Give each a "
            "precise, self-contained question; they report back only their answer. Do the "
            "editing yourself."
        )
    if phases:
        timing = "; ".join(
            f"{phase}: {', '.join(names)}" for phase, names in sorted(phases.items())
        )
        lines.append(f"Your skills, by when they usually matter: {timing}.")
    lines.append(
        "Keep your task list current: write your plan as tasks first and mark each one in "
        "progress and done as you go."
    )
    lines.append(
        "This card is yours: you own it until it lands. Before you finish, check every "
        "acceptance criterion against real evidence, then reflect (the reflect tool): what you "
        "did, how you reasoned, how you feel about it, what you learned, and what you resolve "
        "to do better next time."
    )
    return "\n".join(lines)


def skill_phase(home: Path, slug: str, name: str) -> str:
    path = available_skills(home, slug).get(name)
    return skill_meta(path / "SKILL.md").get("phase", "any") if path else "any"
