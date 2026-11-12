"""A deterministic classifier for the files a change touches.

Input: the changed paths of a diff with their status and line counts, and
optionally the diff text. Output: a :class:`ChangeSet` that gives every file
one or more categories, a risk for each category and an overall risk. No
model is involved, so a model can never lower a classification.

Paths are compared in one normal form: forward slashes, no leading ``./``,
case-folded. A path therefore classifies the same with either separator and
in any letter case, on every platform.

A project can extend the built-in rules in ``.prat/project.toml``
(``[[changes.rule]]``). A project rule adds a category or raises a risk; it
never removes a built-in category and never lowers a risk.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from prat import paths
from prat.git.worktree import DiffStats
from prat.projects import ChangeRule, Changes


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return ("low", "medium", "high").index(self.value)


def highest(*risks: Risk) -> Risk:
    return max(risks, key=lambda risk: risk.rank, default=Risk.LOW)


SECURITY = "security"
DEPENDENCIES = "dependencies"
CI_CONFIG = "ci_config"
SCHEMA = "schema"
CONFIGURATION = "configuration"
TESTS = "tests"
DOCS = "docs"
CODE = "code"
UNKNOWN = "unknown"

BUILTIN_RISK: Mapping[str, Risk] = {
    SECURITY: Risk.HIGH,
    DEPENDENCIES: Risk.HIGH,
    CI_CONFIG: Risk.HIGH,
    SCHEMA: Risk.HIGH,
    CONFIGURATION: Risk.MEDIUM,
    TESTS: Risk.LOW,
    DOCS: Risk.LOW,
    CODE: Risk.MEDIUM,
    UNKNOWN: Risk.HIGH,
}
BUILTIN_CATEGORIES: tuple[str, ...] = tuple(BUILTIN_RISK)


def _names(text: str) -> frozenset[str]:
    """A set of names from one whitespace-separated block."""
    return frozenset(text.split())


# Dependencies ---------------------------------------------------------------------

DEPENDENCY_FILES = _names(
    """
    pyproject.toml setup.py setup.cfg pipfile pipfile.lock poetry.lock uv.lock pdm.lock
    constraints.txt environment.yml environment.yaml
    package.json package-lock.json npm-shrinkwrap.json yarn.lock pnpm-lock.yaml bun.lockb bun.lock
    composer.json composer.lock go.mod go.sum go.work go.work.sum cargo.toml cargo.lock
    gemfile gemfile.lock build.gradle build.gradle.kts settings.gradle settings.gradle.kts
    gradle.lockfile pom.xml mix.exs mix.lock pubspec.yaml pubspec.lock packages.lock.json
    deno.lock flake.lock
    """
)
DEPENDENCY_GLOBS = ("requirements*.txt", "requirements*.in", "*.gemspec", "*.csproj", "*.lock")
DEPENDENCY_DIRS = frozenset({"requirements"})

# CI and deploy --------------------------------------------------------------------

CI_DIRS = _names(".github .gitlab .circleci .buildkite .woodpecker .gitea .forgejo .prat .husky")
CI_FILES = _names(
    """
    .gitlab-ci.yml .travis.yml .drone.yml azure-pipelines.yml bitbucket-pipelines.yml
    appveyor.yml .appveyor.yml jenkinsfile cloudbuild.yaml cloudbuild.yml buildspec.yml
    procfile fly.toml netlify.toml vercel.json render.yaml app.yaml .pre-commit-config.yaml
    """
)
DEPLOY_DIRS = frozenset({"deploy", "deployment", "deployments", ".deploy"})

# Schema ---------------------------------------------------------------------------

SCHEMA_DIRS = frozenset({"migrations", "migration", "alembic", "migrate", "schema", "ddl"})
SCHEMA_FILES = frozenset({"schema.rb", "schema.prisma", "structure.sql", "schema.graphql"})
SCHEMA_EXTENSIONS = frozenset({".sql", ".prisma", ".ddl"})

# Security -------------------------------------------------------------------------

# Matched anywhere inside a path segment. ``auth`` must not be followed by
# ``or`` so that ``authors`` is not security code.
_SECURITY_WORDS = re.compile(
    r"auth(?!or)|session|token|crypt|cipher|polic(?:y|ies)|permission|secret|credential"
    r"|passw|security|login|signin|oauth|jwt|csrf|sandbox|firewall|privilege"
)
# Short names that only count as a whole word of a segment.
_SECURITY_TOKENS = frozenset({"acl", "rbac", "sso", "mfa", "totp", "tls", "ssl", "saml", "guard"})
_WORD_SPLIT = re.compile(r"[^a-z0-9]+")

# Configuration --------------------------------------------------------------------

CONFIG_EXTENSIONS = _names(
    ".toml .ini .cfg .conf .yaml .yml .json .jsonc .properties .env .tf .tfvars .hcl .plist"
)
CONFIG_FILES = _names(
    """
    dockerfile containerfile makefile justfile .editorconfig .gitignore .gitattributes
    .dockerignore .npmrc .nvmrc .python-version .tool-versions .env.example .env.sample
    .env.template agents.md claude.md
    """
)
CONFIG_STEMS = frozenset({"settings", "config", "configuration", "conf"})
CONFIG_DIRS = _names(
    "config configs conf settings infra infrastructure terraform k8s kubernetes helm ansible"
)
# Instruction files read by agents: documentation that also steers behaviour.
AGENT_INSTRUCTION_FILES = frozenset({"agents.md", "claude.md"})

# Tests ----------------------------------------------------------------------------

TEST_DIRS = _names(
    "tests test __tests__ spec specs testing e2e cypress playwright testdata fixtures"
)
TEST_GLOBS = (
    "test_*.py",
    "*_test.py",
    "conftest.py",
    "*_test.go",
    "*.test.js",
    "*.test.jsx",
    "*.test.ts",
    "*.test.tsx",
    "*.test.mjs",
    "*.spec.js",
    "*.spec.jsx",
    "*.spec.ts",
    "*.spec.tsx",
    "*test.php",
    "*test.java",
    "*tests.java",
    "*test.kt",
    "*tests.cs",
    "*test.cs",
    "*_spec.rb",
    "*_test.rb",
    "*_test.rs",
    "*_test.exs",
)
# One test definition per matching line, across the common languages.
_TEST_DEFINITION = re.compile(
    r"^\s*(?:"
    r"(?:async\s+)?def\s+test\w*\s*\("  # Python, Ruby
    r"|(?:it|test|specify)(?:\.\w+)*\s*[(\s]\s*['\"`]"  # JavaScript, TypeScript, Ruby
    r"|func\s+Test\w*\s*\("  # Go
    r"|@(?:Test|ParameterizedTest|RepeatedTest)\b"  # Java, Kotlin
    r"|\[(?:Test|Fact|Theory|TestMethod)\b"  # C#
    r"|#\[(?:\w+::)?test\b"  # Rust
    r"|(?:public\s+|protected\s+|private\s+)?(?:static\s+)?function\s+test\w*\s*\("  # PHP
    r")"
)

# Docs -----------------------------------------------------------------------------

DOC_EXTENSIONS = frozenset({".md", ".mdx", ".rst", ".txt", ".adoc", ".asciidoc"})
DOC_FILES = _names(
    "license licence notice changelog authors contributors contributing readme copying"
)
DOC_DIRS = frozenset({"docs", "doc", "documentation"})
MEDIA_EXTENSIONS = _names(
    ".png .jpg .jpeg .gif .webp .svg .ico .bmp .avif .woff .woff2 .ttf .otf .eot .pdf"
)

# Code -----------------------------------------------------------------------------

CODE_EXTENSIONS = _names(
    """
    .py .pyi .pyx .js .jsx .mjs .cjs .ts .tsx .go .rs .java .kt .kts .scala .groovy .c .h .cc
    .cpp .cxx .hpp .hh .cs .fs .vb .php .rb .erb .ex .exs .erl .swift .m .mm .dart .lua .pl .pm
    .r .jl .hs .clj .cljs .elm .sh .bash .zsh .fish .ps1 .psm1 .bat .cmd .html .htm .css .scss
    .sass .less .vue .svelte .astro .j2 .jinja .jinja2 .twig .blade .mako .tpl .hbs .mustache
    .ejs .graphql .gql .proto .xml .xsl .csv .tsv .ipynb .po .pot
    """
)


@dataclass(frozen=True)
class ChangedPath:
    """One entry of a diff: the classifier's input."""

    path: str
    status: str = "M"  # A, M, D or R
    added: int = 0
    deleted: int = 0
    binary: bool = False
    old_path: str | None = None  # the previous path of a rename


@dataclass(frozen=True)
class FileChange:
    path: str
    status: str
    added: int
    deleted: int
    binary: bool
    categories: dict[str, Risk]
    reasons: list[str] = field(default_factory=list)

    @property
    def risk(self) -> Risk:
        return highest(*self.categories.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "added": self.added,
            "deleted": self.deleted,
            "binary": self.binary,
            "categories": {name: str(risk) for name, risk in self.categories.items()},
            "risk": str(self.risk),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ChangeSet:
    """The classification of one diff, bound to the commits it was computed for."""

    files: list[FileChange]
    base: str | None = None
    head: str | None = None
    tests_deleted: list[str] = field(default_factory=list)
    test_functions_added: int = 0
    test_functions_removed: int = 0
    # Categories in this change that the project says always need a person.
    require_person: list[str] = field(default_factory=list)

    @property
    def categories(self) -> dict[str, Risk]:
        """Every category present, with the highest risk any file gives it."""
        found: dict[str, Risk] = {}
        for change in self.files:
            for name, risk in change.categories.items():
                found[name] = highest(found.get(name, Risk.LOW), risk)
        return dict(sorted(found.items()))

    @property
    def risk(self) -> Risk:
        return highest(*(change.risk for change in self.files))

    @property
    def tests_reduced(self) -> bool:
        """True when a test file was deleted or there are fewer test functions."""
        return bool(self.tests_deleted) or self.test_functions_removed > self.test_functions_added

    @property
    def needs_person(self) -> bool:
        return bool(self.require_person)

    @property
    def flags(self) -> list[str]:
        flags: list[str] = []
        if self.tests_deleted:
            flags.append(f"test files deleted: {', '.join(self.tests_deleted)}")
        if self.test_functions_removed > self.test_functions_added:
            flags.append(
                f"test functions dropped: {self.test_functions_removed} removed, "
                f"{self.test_functions_added} added"
            )
        if self.require_person:
            flags.append(f"needs a person's approval: {', '.join(self.require_person)}")
        return flags

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "head": self.head,
            "risk": str(self.risk),
            "categories": {name: str(risk) for name, risk in self.categories.items()},
            "files": [change.to_dict() for change in self.files],
            "tests_deleted": list(self.tests_deleted),
            "test_functions_added": self.test_functions_added,
            "test_functions_removed": self.test_functions_removed,
            "tests_reduced": self.tests_reduced,
            "require_person": list(self.require_person),
            "flags": self.flags,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ChangeSet:
        files = [
            FileChange(
                path=str(item["path"]),
                status=str(item.get("status", "M")),
                added=int(item.get("added", 0)),
                deleted=int(item.get("deleted", 0)),
                binary=bool(item.get("binary", False)),
                categories={
                    str(name): Risk(risk) for name, risk in dict(item["categories"]).items()
                },
                reasons=[str(reason) for reason in item.get("reasons", [])],
            )
            for item in data.get("files", [])
        ]
        return cls(
            files=files,
            base=data.get("base"),
            head=data.get("head"),
            tests_deleted=[str(path) for path in data.get("tests_deleted", [])],
            test_functions_added=int(data.get("test_functions_added", 0)),
            test_functions_removed=int(data.get("test_functions_removed", 0)),
            require_person=[str(name) for name in data.get("require_person", [])],
        )


def normalise_path(path: str) -> str:
    """Forward slashes, no leading ``./`` or ``/``, case-folded."""
    text = path.strip().replace("\\", "/")
    text = re.sub(r"/{2,}", "/", text)
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/").casefold()


def _extension(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:] if dot > 0 else ""


def _stem(name: str) -> str:
    dot = name.find(".", 1)
    return name[:dot] if dot > 0 else name


def _matches(name: str, globs: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, glob) for glob in globs)


def is_test_path(path: str) -> bool:
    norm = normalise_path(path)
    *dirs, name = norm.split("/")
    return any(part in TEST_DIRS for part in dirs) or _matches(name, TEST_GLOBS)


def _is_doc(dirs: Sequence[str], name: str, binary: bool) -> bool:
    extension = _extension(name)
    if extension in DOC_EXTENSIONS or (not extension and name in DOC_FILES):
        return True
    in_docs = any(part in DOC_DIRS for part in dirs)
    return in_docs and (extension in MEDIA_EXTENSIONS or (not binary and extension == ".html"))


def _security_reason(segments: Sequence[str]) -> str | None:
    for segment in segments:
        found = _SECURITY_WORDS.search(segment)
        if found is not None:
            return f"name contains {found.group(0)!r}"
        for word in _WORD_SPLIT.split(segment):
            if word in _SECURITY_TOKENS:
                return f"name contains {word!r}"
    return None


def builtin_categories(path: str, *, binary: bool = False) -> tuple[dict[str, Risk], list[str]]:
    """The built-in categories of one path, with the reason for each."""
    norm = normalise_path(path)
    *dirs, name = norm.split("/")
    extension = _extension(name)
    found: dict[str, Risk] = {}
    reasons: list[str] = []

    def add(category: str, reason: str) -> None:
        if category not in found:
            found[category] = BUILTIN_RISK[category]
            reasons.append(f"{category}: {reason}")

    if (
        name in DEPENDENCY_FILES
        or _matches(name, DEPENDENCY_GLOBS)
        or (dirs and dirs[-1] in DEPENDENCY_DIRS and extension in {".txt", ".in"})
    ):
        add(DEPENDENCIES, "dependency or lock file")
    if dirs and dirs[0] in CI_DIRS:
        add(CI_CONFIG, f"inside {dirs[0]}/")
    elif name in CI_FILES or any(part in DEPLOY_DIRS for part in dirs):
        add(CI_CONFIG, "CI or deploy configuration")
    if (
        any(part in SCHEMA_DIRS for part in dirs)
        or name in SCHEMA_FILES
        or extension in SCHEMA_EXTENSIONS
    ):
        add(SCHEMA, "database migration or schema file")

    if not found and _is_doc(dirs, name, binary):
        add(DOCS, "documentation")
        if name in AGENT_INSTRUCTION_FILES:
            add(CONFIGURATION, "instruction file read by agents")
        return found, reasons

    if any(part in TEST_DIRS for part in dirs) or _matches(name, TEST_GLOBS):
        add(TESTS, "test file")
    security = _security_reason([*dirs, _stem(name)])
    if security is not None:
        add(SECURITY, security)
    if DEPENDENCIES not in found and CI_CONFIG not in found:
        configuration = (
            extension in CONFIG_EXTENSIONS
            or name in CONFIG_FILES
            or name.startswith(("docker-compose", "compose.", ".env."))
            or _stem(name) in CONFIG_STEMS
            or any(part in CONFIG_DIRS for part in dirs)
        )
        if configuration and TESTS not in found and SCHEMA not in found:
            add(CONFIGURATION, "settings, environment or infrastructure file")

    recognised = extension in CODE_EXTENSIONS or extension in MEDIA_EXTENSIONS
    if binary and extension not in MEDIA_EXTENSIONS:
        add(UNKNOWN, "binary file")
    elif not found:
        if recognised:
            add(CODE, "source file")
        else:
            add(UNKNOWN, "unrecognised file type")
    return found, reasons


def _count_test_functions(diff: str) -> dict[str, tuple[int, int]]:
    """``{normalised test path: (added, removed)}`` test definitions in a unified diff."""
    counts: dict[str, list[int]] = {}
    old: str | None = None
    current: str | None = None
    in_header = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            in_header, old, current = True, None, None
            continue
        if in_header:
            if line.startswith("--- "):
                old = _diff_path(line[4:])
            elif line.startswith("+++ "):
                current = _diff_path(line[4:]) or old
            elif line.startswith("@@"):
                in_header = False
                current = current or old
            continue
        if current is None or not line or line[0] not in "+-":
            continue
        if not is_test_path(current) or not _TEST_DEFINITION.match(line[1:]):
            continue
        entry = counts.setdefault(normalise_path(current), [0, 0])
        entry[0 if line[0] == "+" else 1] += 1
    return {path: (added, removed) for path, (added, removed) in counts.items()}


def _diff_path(raw: str) -> str | None:
    text = raw.strip().strip('"')
    if text == "/dev/null":
        return None
    return text[2:] if text.startswith(("a/", "b/")) else text


def _apply_rules(
    norm: str, categories: dict[str, Risk], reasons: list[str], rules: Sequence[ChangeRule]
) -> None:
    for rule in rules:
        pattern = paths.matches_any(norm, rule.paths, case_insensitive=True)
        if pattern is None:
            continue
        floor = BUILTIN_RISK.get(rule.category, Risk.LOW)
        current = categories.get(rule.category, floor)
        # A rule can only add a category or raise its risk.
        categories[rule.category] = highest(current, floor, Risk(rule.risk))
        reasons.append(f"{rule.category}: project rule {pattern!r}")


def classify(
    files: Iterable[ChangedPath],
    *,
    diff: str = "",
    changes: Changes | None = None,
    base: str | None = None,
    head: str | None = None,
) -> ChangeSet:
    """Classify the changed paths of one diff.

    ``diff`` is the unified diff text. It is only used to count test
    definitions added and removed; classification itself needs paths only.
    """
    rules = list(changes.rule) if changes is not None else []
    wanted = list(changes.require_person) if changes is not None else []
    test_counts = _count_test_functions(diff) if diff else {}
    result: list[FileChange] = []
    tests_deleted: list[str] = []
    for item in files:
        status = (item.status or "M")[:1].upper()
        norm = normalise_path(item.path)
        categories, reasons = builtin_categories(item.path, binary=item.binary)
        _apply_rules(norm, categories, reasons, rules)
        if item.old_path:
            # A rename keeps every category of the path it came from.
            previous, previous_reasons = builtin_categories(item.old_path, binary=item.binary)
            _apply_rules(normalise_path(item.old_path), previous, previous_reasons, rules)
            for name, risk in previous.items():
                categories[name] = highest(categories.get(name, risk), risk)
            reasons.extend(r for r in previous_reasons if r not in reasons)
        if TESTS in categories:
            added, removed = test_counts.get(norm, (0, 0))
            if status == "D":
                tests_deleted.append(item.path.replace("\\", "/"))
                categories[TESTS] = Risk.HIGH
                reasons.append("tests: test file deleted")
            elif removed > added:
                categories[TESTS] = Risk.HIGH
                reasons.append(f"tests: {removed} test functions removed, {added} added")
        result.append(
            FileChange(
                path=item.path.replace("\\", "/"),
                status=status,
                added=item.added,
                deleted=item.deleted,
                binary=item.binary,
                categories=dict(sorted(categories.items())),
                reasons=reasons,
            )
        )
    present = {name for change in result for name in change.categories}
    return ChangeSet(
        files=result,
        base=base,
        head=head,
        tests_deleted=tests_deleted,
        test_functions_added=sum(added for added, _removed in test_counts.values()),
        test_functions_removed=sum(removed for _added, removed in test_counts.values()),
        require_person=[name for name in dict.fromkeys(wanted) if name in present],
    )


def changed_paths(stats: DiffStats) -> list[ChangedPath]:
    """The classifier input for a diff measured by the git layer."""
    return [
        ChangedPath(
            path=file.path,
            status=file.status,
            added=file.added or 0,
            deleted=file.deleted or 0,
            binary=file.binary,
        )
        for file in stats.files
    ]


def classify_diff(stats: DiffStats, diff: str, changes: Changes | None = None) -> ChangeSet:
    return classify(
        changed_paths(stats), diff=diff, changes=changes, base=stats.base, head=stats.head
    )
