"""A project's inventory: everything about its code that tools can work out without a model.

Built from a commit of PRAT's clone (and, for installed versions, the person's checkout):

* every tracked file, classified (code, config, doc, data, lock, binary), with language, size
  and line count;
* modules: readable files grouped by folder into pieces one reader can take in full; large groups
  of data files of one kind (thousands of JSON pages) become a sampled data module instead;
* relationships: imports between files (PRAT's code map) and, when Graphify is installed,
  symbol-level calls and inheritance aggregated to files;
* history: how often each file changed in the last year, and which files change together;
* software: dependencies as declared (requirements, pyproject, package.json, composer.json,
  go.mod), exact installed versions from Syft when it is installed, runtimes (Python and Node
  versions, Docker base images, CI setup), and known vulnerabilities from OSV-Scanner.

Every external tool is optional; what is missing is recorded in ``tools`` and skipped. The same
commit always gives the same inventory.
"""

from __future__ import annotations

import fnmatch
import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prat.git import Git
from prat.knowledge.codemap import build as build_codemap

INVENTORY_FILE = "inventory.json"
MODULE_MAX_FILES = 40
MODULE_MAX_BYTES = 280_000
DATA_GROUP_MIN = 20
DATA_SAMPLES = 3
HISTORY_SINCE = "1 year ago"
COCHANGE_MAX_FILES = 25
COCHANGE_MIN = 3
TOOL_TIMEOUT = 600

CODE_EXT = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".go": "Go",
    ".php": "PHP",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".c": "C",
    ".h": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".scala": "Scala",
    ".lua": "Lua",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".html": "HTML",
    ".htm": "HTML",
    ".jinja": "Jinja",
    ".j2": "Jinja",
    ".twig": "Twig",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sass": "Sass",
    ".less": "Less",
    ".dart": "Dart",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".clj": "Clojure",
}
DOC_EXT = {".md": "Markdown", ".rst": "reStructuredText", ".adoc": "AsciiDoc", ".txt": "Text"}
CONFIG_EXT = {
    ".toml": "TOML",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".ini": "INI",
    ".cfg": "INI",
    ".conf": "Config",
    ".json": "JSON",
    ".xml": "XML",
    ".env.example": "Config",
    ".properties": "Config",
    ".gradle": "Gradle",
    ".plist": "Config",
}
CONFIG_NAMES = {
    "dockerfile",
    "makefile",
    "procfile",
    "gemfile",
    "rakefile",
    "justfile",
    "caddyfile",
    ".gitignore",
    ".dockerignore",
    ".editorconfig",
    ".nvmrc",
    ".node-version",
    ".python-version",
    ".tool-versions",
    "runtime.txt",
    "requirements.txt",
}
LOCK_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pipfile.lock",
    "composer.lock",
    "cargo.lock",
    "go.sum",
    "uv.lock",
    "gemfile.lock",
    "bun.lockb",
}
BINARY_EXT = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".bmp",
    ".tif",
    ".tiff",
    ".avif",
    ".pdf",
    ".zip",
    ".gz",
    ".tgz",
    ".tar",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
    ".mp4",
    ".mov",
    ".webm",
    ".mp3",
    ".wav",
    ".ogg",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pyc",
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".bin",
    ".jar",
    ".class",
    ".fits",
    ".fit",
    ".psd",
    ".ai",
    ".sketch",
    ".keystore",
    ".jks",
    ".apk",
    ".ipa",
    ".npy",
}
DATA_EXT = {".json", ".csv", ".tsv", ".geojson", ".xml", ".yaml", ".yml", ".txt", ".ndjson"}
SKIP_PARTS = {"node_modules", ".venv", "venv", "vendor", "dist", "build", "__pycache__", ".git"}
# Model weights and other opaque artefacts: counted, never read.
MODEL_EXT = {
    ".pt",
    ".pth",
    ".onnx",
    ".h5",
    ".pkl",
    ".pickle",
    ".joblib",
    ".parquet",
    ".arrow",
    ".feather",
    ".tflite",
    ".mlmodel",
    ".safetensors",
    ".ckpt",
    ".npz",
}
# A data file this large is probed, not read; a code file this large is vendored or generated.
LARGE_DATA_BYTES = 100_000
LARGE_CODE_BYTES = 300_000
MISC_MAX_BYTES = 60_000
# No reader is handed a file larger than this, whatever its type.
MAX_READ_BYTES = 1_000_000


@dataclass
class FileInfo:
    path: str
    kind: str  # code, config, doc, data, lock, binary
    language: str
    bytes: int
    lines: int
    module: str = ""


@dataclass
class Module:
    """A piece of the project. ``kind`` code: readable files a reader takes in full. data and
    assets: a top-level folder of data files or binaries, probed by tools, never read by a model
    (``files`` then holds a few samples and ``probe`` what the tools found)."""

    name: str
    files: list[str] = field(default_factory=list)
    bytes: int = 0
    data: bool = False
    data_count: int = 0
    kind: str = "code"
    root: str = ""
    probe: dict[str, Any] = field(default_factory=dict)

    @property
    def folder(self) -> str:
        """The folder this module covers, for linking entries to it."""
        if self.root:
            return self.root
        return self.name.split(" (part ")[0].split(" (group ")[0].rstrip("/")


@dataclass
class Inventory:
    commit: str
    generated_at: str
    files: list[FileInfo] = field(default_factory=list)
    modules: list[Module] = field(default_factory=list)
    languages: dict[str, dict[str, int]] = field(default_factory=dict)
    declared: list[dict[str, str]] = field(default_factory=list)
    packages: list[dict[str, str]] = field(default_factory=list)
    runtimes: list[dict[str, str]] = field(default_factory=list)
    vulnerabilities: list[dict[str, Any]] = field(default_factory=list)
    hotspots: list[dict[str, Any]] = field(default_factory=list)
    cochange: list[dict[str, Any]] = field(default_factory=list)
    imports: list[list[str]] = field(default_factory=list)
    calls: list[list[Any]] = field(default_factory=list)
    symbols: dict[str, int] = field(default_factory=dict)
    tools: dict[str, str] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1, sort_keys=False)

    @classmethod
    def from_json(cls, text: str) -> Inventory:
        raw = json.loads(text)
        raw["files"] = [FileInfo(**f) for f in raw.get("files", [])]
        raw["modules"] = [Module(**m) for m in raw.get("modules", [])]
        return cls(**raw)

    def file(self, path: str) -> FileInfo | None:
        return next((f for f in self.files if f.path == path), None)

    @property
    def readable(self) -> list[FileInfo]:
        return [f for f in self.files if f.kind in ("code", "config", "doc")]

    def summary(self) -> str:
        """The inventory as text for a model: what tools found, compactly."""
        kinds = Counter(f.kind for f in self.files)
        lines = [
            f"Commit {self.commit[:12]}. {len(self.files)} tracked files: "
            + ", ".join(f"{n} {k}" for k, n in kinds.most_common())
            + ".",
            "Languages (files, lines): "
            + ", ".join(
                f"{lang} {v['files']}/{v['lines']}"
                for lang, v in sorted(self.languages.items(), key=lambda kv: -kv[1]["lines"])[:12]
            ),
        ]
        if self.runtimes:
            lines.append(
                "Runtimes: "
                + "; ".join(f"{r['what']} {r['version']} ({r['file']})" for r in self.runtimes)
            )
        if self.declared:
            lines.append(
                "Declared dependencies: "
                + ", ".join(f"{d['name']}{d['spec']}" for d in self.declared[:60])
            )
        if self.packages:
            lines.append(
                f"Installed packages ({len(self.packages)}): "
                + ", ".join(f"{p['name']} {p['version']}" for p in self.packages[:80])
            )
        if self.vulnerabilities:
            lines.append(
                "Known vulnerabilities: "
                + "; ".join(
                    f"{v['package']} {v['version']}: {', '.join(v['ids'][:4])}"
                    for v in self.vulnerabilities
                )
            )
        if self.hotspots:
            lines.append(
                "Most changed files in the last year: "
                + ", ".join(f"{h['path']} ({h['commits']})" for h in self.hotspots[:15])
            )
        if self.cochange:
            lines.append(
                "Files that change together: "
                + "; ".join(f"{c['a']} + {c['b']} ({c['together']})" for c in self.cochange[:15])
            )
        lines.append(
            "Code modules: "
            + "; ".join(f"{m.name} ({len(m.files)} files)" for m in self.modules if not m.data)
        )
        for m in self.modules:
            if m.data:
                lines.append(f"{m.kind.capitalize()}: {describe_group(m)}")
        return "\n".join(lines)


# Files ---------------------------------------------------------------------------------------


def _classify(path: str, data_groups: set[tuple[str, str]], size: int = 0) -> tuple[str, str]:
    name = posixpath.basename(path).lower()
    ext = posixpath.splitext(name)[1]
    folder = posixpath.dirname(path)
    if name in LOCK_NAMES:
        return "lock", "Lockfile"
    if ext in BINARY_EXT or ext in MODEL_EXT:
        return "binary", ext.lstrip(".").upper()
    if name.endswith((".min.js", ".min.css")) or (ext in CODE_EXT and size > LARGE_CODE_BYTES):
        return "binary", "Vendored"
    if ext in DATA_EXT and ((folder, ext) in data_groups or size > LARGE_DATA_BYTES):
        return "data", ext.lstrip(".").upper()
    if name in CONFIG_NAMES or (name.startswith("requirements") and ext == ".txt"):
        return "config", "Config"
    if name.endswith(".blade.php"):
        return "code", "Blade"
    if ext in CODE_EXT:
        return "code", CODE_EXT[ext]
    if ext in DOC_EXT:
        return "doc", DOC_EXT[ext]
    if ext in CONFIG_EXT or name.startswith(".env"):
        return "config", CONFIG_EXT.get(ext, "Config")
    return "config", "Other"


def _list_files(repo: Path, commit: str) -> list[tuple[str, int]]:
    out = Git(repo).run("ls-tree", "-r", "-l", "-z", commit, check=False)
    found = []
    for record in out.stdout.split("\0"):
        if not record.strip():
            continue
        meta, _, path = record.partition("\t")
        parts = meta.split()
        if len(parts) < 4 or parts[1] != "blob":
            continue
        if any(part in SKIP_PARTS for part in path.split("/")[:-1]):
            continue
        size = int(parts[3]) if parts[3].isdigit() else 0
        found.append((path, size))
    return found


def _count_lines(repo: Path, path: str, size: int) -> int:
    if size > 5_000_000:
        return 0
    try:
        with open(repo / path, "rb") as handle:
            return handle.read().count(b"\n")
    except OSError:
        return 0


def classify_files(repo: Path, commit: str) -> list[FileInfo]:
    listed = _list_files(repo, commit)
    groups = Counter(
        (posixpath.dirname(p), posixpath.splitext(p.lower())[1])
        for p, _ in listed
        if posixpath.splitext(p.lower())[1] in DATA_EXT
    )
    data_groups = {key for key, n in groups.items() if n >= DATA_GROUP_MIN}
    files = []
    for path, size in listed:
        kind, language = _classify(path, data_groups, size)
        if kind in ("code", "config", "doc") and (
            size > MAX_READ_BYTES or _looks_binary(repo / path)
        ):
            # An unknown format or an outsized file is counted, never handed to a reader.
            kind, language = "binary", "Large or binary"
        lines = _count_lines(repo, path, size) if kind in ("code", "config", "doc") else 0
        files.append(FileInfo(path, kind, language, size, lines))
    return files


def _looks_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return b"\0" in handle.read(8192)
    except OSError:
        return False


# Modules -------------------------------------------------------------------------------------


def _split(name: str, files: list[FileInfo], depth: int) -> list[Module]:
    total = sum(f.bytes for f in files)
    if len(files) <= MODULE_MAX_FILES and total <= MODULE_MAX_BYTES:
        return [Module(name, [f.path for f in files], total)]
    prefix = "" if name == "(root)" else name.rstrip("/") + "/"
    direct = [f for f in files if "/" not in f.path[len(prefix) :]]
    nested: dict[str, list[FileInfo]] = defaultdict(list)
    for f in files:
        rest = f.path[len(prefix) :]
        if "/" in rest:
            nested[prefix + rest.split("/")[0]].append(f)
    modules: list[Module] = []
    if nested and depth < 6:
        for sub, members in sorted(nested.items()):
            modules += _split(sub, members, depth + 1)
    else:
        direct = files
    # Files directly in this folder: in pieces that fit.
    chunk: list[FileInfo] = []
    part = 1
    for f in sorted(direct, key=lambda x: x.path):
        if chunk and (
            len(chunk) >= MODULE_MAX_FILES
            or sum(c.bytes for c in chunk) + f.bytes > MODULE_MAX_BYTES
        ):
            modules.append(
                Module(
                    f"{name} (part {part})", [c.path for c in chunk], sum(c.bytes for c in chunk)
                )
            )
            chunk, part = [], part + 1
        chunk.append(f)
    if chunk:
        label = f"{name} (part {part})" if part > 1 else name
        modules.append(Module(label, [c.path for c in chunk], sum(c.bytes for c in chunk)))
    return modules


SMALL_MODULE = 12


def _top(path: str) -> str:
    return path.split("/")[0] if "/" in path else "(root)"


def _pack(modules: list[Module]) -> list[Module]:
    """Small modules of one top-level folder are packed together (first fit, largest first) up
    to the limits, so a reader's start-up time is not paid for every little folder."""
    packed: list[Module] = []
    small: dict[str, list[Module]] = defaultdict(list)
    for m in modules:
        if len(m.files) <= SMALL_MODULE and m.bytes <= MODULE_MAX_BYTES // 2:
            small[_top(m.files[0])].append(m)
        else:
            packed.append(m)
    for top, members in sorted(small.items()):
        bins: list[list[Module]] = []
        for m in sorted(members, key=lambda x: -x.bytes):
            for b in bins:
                if (
                    sum(len(x.files) for x in b) + len(m.files) <= MODULE_MAX_FILES
                    and sum(x.bytes for x in b) + m.bytes <= MODULE_MAX_BYTES
                ):
                    b.append(m)
                    break
            else:
                bins.append([m])
        for n, b in enumerate(bins, start=1):
            if len(b) == 1:
                packed.append(b[0])
                continue
            files = sorted(f for x in b for f in x.files)
            name = f"{top} (group {n})"
            packed.append(
                Module(name, files, sum(x.bytes for x in b), root="" if top == "(root)" else top)
            )
    # Odds and ends left alone in tiny folders (one workflow file, one doc) share a reader.
    loose = [m for m in packed if len(m.files) <= 3 and m.bytes <= MISC_MAX_BYTES]
    if len(loose) > 1:
        packed = [m for m in packed if m not in loose]
        misc_bins: list[list[Module]] = [[]]
        for m in loose:
            if sum(x.bytes for x in misc_bins[-1]) + m.bytes > MODULE_MAX_BYTES or (
                sum(len(x.files) for x in misc_bins[-1]) + len(m.files) > MODULE_MAX_FILES
            ):
                misc_bins.append([])
            misc_bins[-1].append(m)
        for n, b in enumerate(misc_bins, start=1):
            files = sorted(f for x in b for f in x.files)
            packed.append(Module(f"(misc group {n})", files, sum(x.bytes for x in b)))
    return sorted(packed, key=lambda m: m.name)


def build_modules(files: list[FileInfo]) -> list[Module]:
    readable = [f for f in files if f.kind in ("code", "config", "doc")]
    by_top: dict[str, list[FileInfo]] = defaultdict(list)
    for f in readable:
        by_top[_top(f.path)].append(f)
    code: list[Module] = []
    for top, members in sorted(by_top.items()):
        code += _split(top, members, 0)
    modules = _pack(code)
    names = {m.name for m in modules}
    owner: dict[str, str] = {path: m.name for m in modules for path in m.files}
    # Data and assets: one probed module per top-level folder, however deep it goes.
    code_tops = {_top(f.path) for f in readable}
    for kind in ("data", "binary"):
        groups: dict[str, list[FileInfo]] = defaultdict(list)
        for f in files:
            if f.kind == kind:
                parts = f.path.split("/")
                # Inside a code folder, group by its subfolder (app/static), not all of app/.
                key = (
                    "/".join(parts[:2])
                    if parts[0] in code_tops and len(parts) > 2
                    else _top(f.path)
                )
                groups[key].append(f)
        for top, members in sorted(groups.items()):
            label = "data" if kind == "data" else "assets"
            name = top if top not in names else f"{top} ({label})"
            names.add(name)
            members.sort(key=lambda x: x.path)
            step = max(1, len(members) // DATA_SAMPLES)
            samples = [m.path for m in members[::step][:DATA_SAMPLES]] if kind == "data" else []
            modules.append(
                Module(
                    name,
                    samples,
                    sum(m.bytes for m in members),
                    data=True,
                    data_count=len(members),
                    kind=label,
                    root="" if top == "(root)" else top,
                )
            )
            for m in members:
                owner[m.path] = name
    for f in files:
        f.module = owner.get(f.path, "")
    return modules


def _shape(value: Any, depth: int = 0) -> str:
    if isinstance(value, dict):
        if depth >= 1:
            return "object"
        keys = list(value)[:14]
        inner = ", ".join(f"{k}: {_shape(value[k], depth + 1)}" for k in keys)
        more = ", ..." if len(value) > len(keys) else ""
        return "{" + inner + more + "}"
    if isinstance(value, list):
        return f"list of {_shape(value[0], depth + 1)}" if value else "list"
    return type(value).__name__.replace("NoneType", "null").replace("str", "text")


def probe_groups(repo: Path, inv: Inventory) -> None:
    """What tools can say about each data and assets folder: how it is laid out, what its files
    look like, and which code files refer to it. No model; milliseconds."""
    code_texts: dict[str, str] = {}
    for f in inv.files:
        if f.kind == "code" and f.bytes <= 500_000:
            try:
                code_texts[f.path] = (repo / f.path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    for m in inv.modules:
        if not m.data:
            continue
        members = [f for f in inv.files if f.module == m.name]
        depth = len(m.root.split("/")) if m.root else 0
        sub_folders = Counter(
            f.path.split("/")[depth] if len(f.path.split("/")) > depth + 1 else "(top)"
            for f in members
        )
        extensions = Counter(posixpath.splitext(f.path)[1].lower() or "(none)" for f in members)
        probe: dict[str, Any] = {
            "extensions": dict(extensions.most_common(6)),
            "folders": dict(sub_folders.most_common(8)),
            "folder_count": len(sub_folders),
        }
        shapes: list[str] = []
        for path in m.files:
            ext = posixpath.splitext(path)[1].lower()
            try:
                text = (repo / path).read_text(encoding="utf-8", errors="replace")[:400_000]
                if ext in (".json", ".geojson"):
                    shapes.append(_shape(json.loads(text)))
                elif ext in (".csv", ".tsv"):
                    shapes.append("columns: " + text.splitlines()[0][:300])
                elif ext == ".ndjson":
                    shapes.append(_shape(json.loads(text.splitlines()[0])))
            except (OSError, ValueError, IndexError):
                continue
        probe["shapes"] = list(dict.fromkeys(shapes))[:3]
        if m.root:
            quote = "[\"'`/(]"
            name = m.root.rsplit("/", 1)[-1]
            pattern = re.compile(quote + re.escape(name) + "[\"'`/)]")
            probe["used_by"] = sorted(p for p, t in code_texts.items() if pattern.search(t))[:12]
        m.probe = probe


def describe_group(m: Module) -> str:
    probe = m.probe
    where = f"{m.root}/" if m.root else "the repository root"
    exts = ", ".join(f"{n} {e}" for e, n in (probe.get("extensions") or {}).items())
    text = f"{where} holds {m.data_count} {m.kind} files ({exts})"
    folders = probe.get("folders") or {}
    if folders and probe.get("folder_count", 0) > 1:
        shown = ", ".join(f"{k} ({v})" for k, v in folders.items())
        text += f" in {probe['folder_count']} folders, largest: {shown}"
    if probe.get("shapes"):
        text += ". Files look like: " + " | ".join(probe["shapes"])
    if probe.get("used_by"):
        text += ". Referred to in: " + ", ".join(probe["used_by"])
    return text + "."


# Dependencies and versions -------------------------------------------------------------------


_REQ = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]]+)\s*([<>=!~^].*)?$")


def _declared(repo: Path, files: Iterable[FileInfo]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []

    def add(name: str, spec: str, file: str) -> None:
        found.append({"name": name.strip(), "spec": (spec or "").strip(), "file": file})

    for f in files:
        name = posixpath.basename(f.path).lower()
        full = repo / f.path
        try:
            if name.startswith("requirements") and name.endswith(".txt"):
                for line in full.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.split("#")[0].strip()
                    if line and not line.startswith(("-", "git+", "http")):
                        m = _REQ.match(line)
                        if m:
                            add(m.group(1), m.group(2) or "", f.path)
            elif name == "pyproject.toml":
                data = tomllib.loads(full.read_text(encoding="utf-8"))
                project = data.get("project", {})
                groups = [
                    project.get("dependencies", []),
                    *(project.get("optional-dependencies") or {}).values(),
                ]
                for group in groups:
                    for dep in group:
                        m = _REQ.match(re.split(r";", dep)[0])
                        if m:
                            add(m.group(1), m.group(2) or "", f.path)
                poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
                for dep, spec in poetry.items():
                    add(dep, spec if isinstance(spec, str) else json.dumps(spec), f.path)
            elif name == "package.json":
                data = json.loads(full.read_text(encoding="utf-8"))
                for key in ("dependencies", "devDependencies", "peerDependencies"):
                    for dep, spec in (data.get(key) or {}).items():
                        add(dep, str(spec), f.path)
            elif name == "composer.json":
                data = json.loads(full.read_text(encoding="utf-8"))
                for key in ("require", "require-dev"):
                    for dep, spec in (data.get(key) or {}).items():
                        add(dep, str(spec), f.path)
            elif name == "go.mod":
                for line in full.read_text(encoding="utf-8").splitlines():
                    m = re.match(r"^\s*(?:require\s+)?([\w./\-]+\.[\w./\-]+)\s+(v[\w.\-+]+)", line)
                    if m and not line.strip().startswith(("module", "go ")):
                        add(m.group(1), m.group(2), f.path)
        except (OSError, ValueError, tomllib.TOMLDecodeError):
            continue
    return found


def _runtimes(repo: Path, files: Iterable[FileInfo]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(what: str, version: str, file: str) -> None:
        key = (what, version)
        if version and key not in seen:
            seen.add(key)
            found.append({"what": what, "version": version.strip(), "file": file})

    for f in files:
        name = posixpath.basename(f.path).lower()
        full = repo / f.path
        try:
            text = full.read_text(encoding="utf-8", errors="replace") if f.bytes < 500_000 else ""
        except OSError:
            continue
        if name == "pyproject.toml":
            m = re.search(r'requires-python\s*=\s*"([^"]+)"', text)
            if m:
                add("Python", m.group(1), f.path)
        elif name in (".python-version", "runtime.txt"):
            add("Python", text.strip().replace("python-", ""), f.path)
        elif name in (".nvmrc", ".node-version"):
            add("Node", text.strip(), f.path)
        elif name == "package.json":
            try:
                engines = json.loads(text).get("engines") or {}
            except ValueError:
                engines = {}
            for what, version in engines.items():
                add(what.capitalize(), str(version), f.path)
        elif name == "dockerfile" or name.endswith(".dockerfile"):
            for m in re.finditer(r"^FROM\s+(\S+)", text, re.M):
                add("Docker base image", m.group(1), f.path)
        elif f.path.startswith(".github/workflows/"):
            for what, key in (
                ("Python", "python-version"),
                ("Node", "node-version"),
                ("Go", "go-version"),
                ("PHP", "php-version"),
            ):
                for m in re.finditer(rf"{key}:\s*['\"]?([\w.\-x*]+)", text):
                    add(f"{what} in CI", m.group(1), f.path)
    return found


def _which(name: str, home: Path | None) -> str | None:
    if name == "graphify" and home is not None:
        bundled = home / "tools" / "graphify" / "bin" / "graphify"
        if bundled.is_file():
            return str(bundled)
    return shutil.which(name)


def _version(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    text = (out.stdout or out.stderr).strip().splitlines()
    return text[0][:80] if text else "unknown"


SYFT_TYPES = {
    "python",
    "npm",
    "go-module",
    "rust-crate",
    "php-composer",
    "gem",
    "java-archive",
    "dotnet",
    "pub",
    "hex",
    "cocoapods",
    "swift",
    "github-action",
}

# Which tracked files make an ecosystem the project's own. Syft also reports packages that ship
# inside other packages (React Native's node_modules carries .NET, Java and Swift manifests);
# those are not the project's dependencies and must not become knowledge.
ECOSYSTEM_FILES: dict[str, tuple[str, ...]] = {
    "python": (
        "requirements*.txt",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "pipfile",
        "uv.lock",
        "poetry.lock",
    ),
    "npm": ("package.json",),
    "go-module": ("go.mod",),
    "rust-crate": ("cargo.toml",),
    "php-composer": ("composer.json",),
    "gem": ("gemfile", "*.gemspec"),
    "java-archive": ("pom.xml", "build.gradle", "build.gradle.kts"),
    "dotnet": ("*.csproj", "*.fsproj", "*.sln", "packages.config"),
    "pub": ("pubspec.yaml",),
    "hex": ("mix.exs",),
    "cocoapods": ("podfile",),
    "swift": ("package.swift",),
}
# Folders where installed packages live; a manifest found inside one belongs to that package.
VENDORED = ("node_modules/", "site-packages/", "/vendor/", ".venv/", "venv/", "Pods/")


def own_ecosystems(files: Iterable[FileInfo]) -> set[str]:
    """The ecosystems the project itself declares, from its tracked files."""
    found: set[str] = set()
    for f in files:
        name = posixpath.basename(f.path).lower()
        if any(part in f"/{f.path}" for part in ("/node_modules/", "/vendor/", "/Pods/")):
            continue
        for ecosystem, patterns in ECOSYSTEM_FILES.items():
            if any(fnmatch.fnmatch(name, p) for p in patterns):
                found.add(ecosystem)
        if f.path.startswith(".github/workflows/"):
            found.add("github-action")
    return found


def own_packages(packages: list[dict[str, str]], ecosystems: set[str]) -> list[dict[str, str]]:
    """Packages of the project's own ecosystems. A GitHub Action counts only when the project's
    own workflows use it, not when it was found inside an installed package."""
    kept = []
    for package in packages:
        kind, where = package.get("type", ""), package.get("where", "")
        if kind not in ecosystems:
            continue
        if kind == "github-action" and any(v in where for v in VENDORED):
            continue
        kept.append(package)
    return kept


def _syft(syft: str, source: Path, scratch: Path) -> tuple[list[dict[str, str]], Path | None]:
    try:
        out = subprocess.run(
            [
                syft,
                "scan",
                f"dir:{source}",
                "-o",
                "json",
                "-q",
                "-o",
                f"cyclonedx-json={scratch / 'bom.cdx.json'}",
            ],
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT,
            check=False,
        )
        artifacts = json.loads(out.stdout or "{}").get("artifacts", [])
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return [], None
    packages = {}
    for a in artifacts:
        if a.get("type") not in SYFT_TYPES:
            continue
        key = (a.get("name", ""), a.get("version", ""), a.get("type", ""))
        location = (a.get("locations") or [{}])[0].get("path", "")
        packages[key] = {"name": key[0], "version": key[1], "type": key[2], "where": location[:120]}
    bom = scratch / "bom.cdx.json"
    return sorted(
        packages.values(), key=lambda p: (p["type"], p["name"].lower())
    ), bom if bom.is_file() else None


def _osv(osv: str, bom: Path) -> list[dict[str, Any]]:
    try:
        out = subprocess.run(
            [osv, "scan", "source", "-L", str(bom), "--format", "json"],
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT,
            check=False,
        )
        data = json.loads(out.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    found = []
    for result in data.get("results") or []:
        for pkg in result.get("packages") or []:
            ids = [v.get("id", "") for v in pkg.get("vulnerabilities") or []]
            if ids:
                found.append(
                    {
                        "package": pkg["package"]["name"],
                        "version": pkg["package"].get("version", ""),
                        "ids": ids,
                    }
                )
    return found


# History and relationships -------------------------------------------------------------------


def _history(repo: Path, readable: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    out = Git(repo).run(
        "log",
        f"--since={HISTORY_SINCE}",
        "--name-only",
        "--no-merges",
        "--format=%x1e",
        check=False,
    )
    churn: Counter[str] = Counter()
    pairs: Counter[tuple[str, str]] = Counter()
    for commit in out.stdout.split("\x1e"):
        changed = sorted({line.strip() for line in commit.splitlines() if line.strip() in readable})
        churn.update(changed)
        if 1 < len(changed) <= COCHANGE_MAX_FILES:
            for i, a in enumerate(changed):
                for b in changed[i + 1 :]:
                    pairs[(a, b)] += 1
    hotspots = [{"path": p, "commits": n} for p, n in churn.most_common(40)]
    cochange = [
        {"a": a, "b": b, "together": n} for (a, b), n in pairs.most_common(60) if n >= COCHANGE_MIN
    ]
    return hotspots, cochange


def _graphify(
    graphify: str, repo: Path, commit: str, scratch: Path
) -> tuple[list[list[Any]], dict[str, int]]:
    """Symbol-level relationships from Graphify (code only, no model), summed per file pair."""
    copy = scratch / "graphify-src"
    copy.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", commit], capture_output=True, check=False
    )
    if archive.returncode != 0:
        return [], {}
    subprocess.run(
        ["tar", "-x", "-C", str(copy)], input=archive.stdout, capture_output=True, check=False
    )
    env = {**os.environ, "GRAPHIFY_BACKEND": "none", "GRAPHIFY_QUERY_LOG_DISABLE": "1"}
    try:
        subprocess.run(
            [graphify, "update", "."],
            cwd=copy,
            env=env,
            capture_output=True,
            timeout=TOOL_TIMEOUT,
            check=False,
        )
        graph = json.loads((copy / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return [], {}
    where = {
        n["id"]: n.get("source_file", "") for n in graph.get("nodes", []) if n.get("source_file")
    }
    symbols = Counter(
        n["source_file"]
        for n in graph.get("nodes", [])
        if n.get("file_type") == "code" and n.get("source_file")
    )
    calls: Counter[tuple[str, str, str]] = Counter()
    for link in graph.get("links", []):
        relation = link.get("relation", "")
        if relation not in ("calls", "inherits", "uses"):
            continue
        a, b = where.get(link.get("source"), ""), where.get(link.get("target"), "")
        if a and b and a != b:
            calls[(a, b, relation)] += 1
    return [[a, b, rel, n] for (a, b, rel), n in calls.most_common(2000)], dict(symbols)


# Build ---------------------------------------------------------------------------------------


def build_inventory(
    repo: Path, commit: str, *, installed: Path | None = None, home: Path | None = None
) -> Inventory:
    """The inventory of ``commit`` in ``repo``. ``installed`` is a checkout with dependencies
    installed (its .venv or node_modules give exact versions)."""
    timings: dict[str, float] = {}
    clock = time.monotonic()

    def lap(name: str) -> None:
        nonlocal clock
        now = time.monotonic()
        timings[name] = round(now - clock, 2)
        clock = now

    inv = Inventory(commit=commit, generated_at=datetime.now(UTC).isoformat(timespec="seconds"))
    inv.files = classify_files(repo, commit)
    inv.modules = build_modules(inv.files)
    probe_groups(repo, inv)
    languages: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "lines": 0})
    for f in inv.files:
        if f.kind in ("code", "config", "doc"):
            languages[f.language]["files"] += 1
            languages[f.language]["lines"] += f.lines
    inv.languages = dict(languages)
    lap("files")
    readable = {f.path for f in inv.readable}
    code = build_codemap(repo, commit)
    inv.imports = [[a, b] for a, b in code.edges]
    lap("imports")
    inv.hotspots, inv.cochange = _history(repo, readable)
    lap("history")
    inv.declared = _declared(repo, inv.readable)
    inv.runtimes = _runtimes(repo, [f for f in inv.files if f.kind in ("config", "code", "doc")])
    lap("declared")
    with tempfile.TemporaryDirectory(prefix="prat-inventory-") as tmp:
        scratch = Path(tmp)
        syft, osv, graphify = (
            _which("syft", home),
            _which("osv-scanner", home),
            _which("graphify", home),
        )
        inv.tools = {
            "syft": _version([syft, "version"]) if syft else "missing",
            "osv-scanner": _version([osv, "--version"]) if osv else "missing",
            "graphify": "installed" if graphify else "missing",
        }
        bom = None
        ecosystems = own_ecosystems(inv.files)
        if syft:
            found, bom = _syft(syft, installed or repo, scratch)
            inv.packages = own_packages(found, ecosystems)
        lap("packages")
        if osv and bom is not None:
            kept = {p["name"] for p in inv.packages}
            inv.vulnerabilities = [v for v in _osv(osv, bom) if v["package"] in kept]
        lap("vulnerabilities")
        if graphify:
            inv.calls, inv.symbols = _graphify(graphify, repo, commit, scratch)
        lap("symbols")
    inv.timings = timings
    return inv


def inventory_path(kb_root: Path) -> Path:
    return kb_root / INVENTORY_FILE


def load_inventory(kb_root: Path) -> Inventory | None:
    try:
        return Inventory.from_json(inventory_path(kb_root).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, KeyError):
        return None


def save_inventory(kb_root: Path, inv: Inventory) -> None:
    kb_root.mkdir(parents=True, exist_ok=True)
    path = inventory_path(kb_root)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(inv.to_json(), encoding="utf-8", newline="\n")
    tmp.replace(path)
