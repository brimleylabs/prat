"""A project's code map: files, the imports between them and their top-level definitions,
ranked by personalised PageRank toward what a card is about.

Built from a commit of PRAT's clone without a model (in the spirit of Aider's repository map)
and cached per commit. Imports are read with Python's own parser for Python and with simple
patterns for JavaScript and TypeScript, Go, PHP, Ruby and Rust; only imports that resolve to a
file in the repository become edges. The map is approximate by design: it decides what to put
in front of an agent first, not what is correct.
"""

from __future__ import annotations

import ast
import json
import os
import posixpath
import re
import subprocess
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from prat.git import Git

SOURCE_EXTENSIONS = {
    ".py": "python",
    ".js": "js",
    ".jsx": "js",
    ".mjs": "js",
    ".cjs": "js",
    ".ts": "js",
    ".tsx": "js",
    ".vue": "js",
    ".svelte": "js",
    ".go": "go",
    ".php": "php",
    ".rb": "ruby",
    ".rs": "rust",
}
SKIP_DIRS = {
    "node_modules",
    "vendor",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    "target",
    ".next",
    "coverage",
}
MAX_FILES = 6000
MAX_BYTES = 200_000
MAX_DEFINITIONS = 40
JS_IMPORT = re.compile(
    r"""(?:import\s[^'"]*?from\s*|import\s*\(\s*|require\s*\(\s*|export\s[^'"]*?from\s*|import\s+)['"]([^'"]+)['"]"""
)
JS_DEFINITION = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\s*\*?|class|const|let|interface|type|enum)\s+([A-Za-z_$][\w$]*)",
    re.M,
)
GO_IMPORT_BLOCK = re.compile(r"^import\s*\((.*?)\)", re.S | re.M)
GO_IMPORT_LINE = re.compile(r'^import\s+(?:\w+\s+)?"([^"]+)"', re.M)
GO_DEFINITION = re.compile(r"^(?:func(?:\s*\([^)]*\))?|type)\s+([A-Za-z_]\w*)", re.M)
PHP_USE = re.compile(r"^\s*use\s+([A-Za-z_][\w\\]*)", re.M)
PHP_INCLUDE = re.compile(r"""(?:require|include)(?:_once)?\s*\(?\s*['"]([^'"]+\.php)['"]""")
PHP_DEFINITION = re.compile(
    r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait|enum|function)\s+([A-Za-z_]\w*)",
    re.M,
)
RUBY_REQUIRE = re.compile(r"""^\s*require_relative\s+['"]([^'"]+)['"]""", re.M)
RUBY_DEFINITION = re.compile(r"^\s*(?:class|module|def)\s+([A-Za-z_][\w.:]*)", re.M)
RUST_MOD = re.compile(r"^\s*(?:pub\s+)?mod\s+([a-z_]\w*)\s*;", re.M)
RUST_DEFINITION = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:fn|struct|enum|trait|type)\s+([A-Za-z_]\w*)", re.M
)
JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")


@dataclass
class CodeMap:
    commit: str
    files: list[str] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    definitions: dict[str, list[str]] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "commit": self.commit,
                "files": self.files,
                "edges": self.edges,
                "definitions": self.definitions,
            }
        )

    @classmethod
    def from_json(cls, text: str) -> CodeMap:
        raw = json.loads(text)
        return cls(
            str(raw["commit"]),
            [str(f) for f in raw["files"]],
            [(str(a), str(b)) for a, b in raw["edges"]],
            {str(k): [str(v) for v in vs] for k, vs in raw["definitions"].items()},
        )

    def neighbours(self, path: str) -> set[str]:
        return {b for a, b in self.edges if a == path} | {a for a, b in self.edges if b == path}


# Reading the repository -------------------------------------------------------------------


def _wanted(path: str) -> bool:
    parts = path.split("/")
    if any(part in SKIP_DIRS for part in parts[:-1]):
        return False
    return posixpath.splitext(path)[1].lower() in SOURCE_EXTENSIONS


def _blobs(repo: Path, commit: str) -> dict[str, str]:
    listing = Git(repo).run("ls-tree", "-r", "-z", "--full-tree", commit)
    blobs: dict[str, str] = {}
    for record in filter(None, listing.stdout.split("\0")):
        meta, _, path = record.partition("\t")
        kind, sha = meta.split()[1:3]
        if kind == "blob" and _wanted(path):
            blobs[path] = sha
            if len(blobs) >= MAX_FILES:
                break
    return blobs


def _contents(repo: Path, blobs: Mapping[str, str]) -> dict[str, str]:
    """File contents in one ``git cat-file --batch`` call."""
    if not blobs:
        return {}
    request = "".join(f"{sha}\n" for sha in blobs.values()).encode()
    result = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=repo,
        input=request,
        capture_output=True,
        check=False,
        timeout=300,
    )
    out = result.stdout
    by_sha: dict[str, str] = {}
    pos = 0
    while pos < len(out):
        end = out.index(b"\n", pos)
        header = out[pos:end].decode()
        pos = end + 1
        parts = header.split()
        if len(parts) < 3 or parts[1] == "missing":
            continue
        size = int(parts[2])
        data = out[pos : pos + size]
        pos += size + 1
        if size <= MAX_BYTES:
            by_sha[parts[0]] = data.decode("utf-8", errors="replace")
    return {path: by_sha[sha] for path, sha in blobs.items() if sha in by_sha}


# Imports and definitions per language ------------------------------------------------------


def _python(path: str, text: str, files: set[str]) -> tuple[set[str], list[str]]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set(), []
    names: list[str] = []
    targets: set[str] = set()
    package = posixpath.dirname(path)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.append(node.name)
    for found in ast.walk(tree):
        modules: list[str] = []
        if isinstance(found, ast.Import):
            modules = [alias.name for alias in found.names]
        elif isinstance(found, ast.ImportFrom):
            base = found.module or ""
            if found.level:
                parent = package
                for _ in range(found.level - 1):
                    parent = posixpath.dirname(parent)
                prefix = parent.replace("/", ".")
                base = f"{prefix}.{base}".strip(".") if base else prefix
                modules = [base] + [f"{base}.{a.name}" for a in found.names]
            else:
                modules = [base] + [f"{base}.{a.name}" for a in found.names]
        for module in modules:
            target = _python_file(module, files)
            if target is not None and target != path:
                targets.add(target)
    return targets, names


def _python_file(module: str, files: set[str]) -> str | None:
    if not module:
        return None
    stem = module.replace(".", "/")
    for root in ("", "src/", "lib/", "app/"):
        for candidate in (f"{root}{stem}.py", f"{root}{stem}/__init__.py"):
            if candidate in files:
                return candidate
    return None


def _resolve_relative(path: str, spec: str, files: set[str], exts: Iterable[str]) -> str | None:
    if not spec.startswith((".", "/")):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(path), spec)).lstrip("/")
    if base in files:
        return base
    for ext in exts:
        if f"{base}{ext}" in files:
            return f"{base}{ext}"
        if f"{base}/index{ext}" in files:
            return f"{base}/index{ext}"
    return None


def _js(path: str, text: str, files: set[str]) -> tuple[set[str], list[str]]:
    targets = set()
    for spec in JS_IMPORT.findall(text):
        target = _resolve_relative(path, spec, files, JS_EXTENSIONS)
        if target is not None and target != path:
            targets.add(target)
    return targets, JS_DEFINITION.findall(text)


def _go(path: str, text: str, files: set[str], go_module: str) -> tuple[set[str], list[str]]:
    specs = GO_IMPORT_LINE.findall(text)
    for block in GO_IMPORT_BLOCK.findall(text):
        specs += re.findall(r'"([^"]+)"', block)
    targets = set()
    for spec in specs:
        if go_module and spec.startswith(go_module + "/"):
            folder = spec[len(go_module) + 1 :]
            targets |= {
                f
                for f in files
                if posixpath.dirname(f) == folder and f.endswith(".go") and f != path
            }
    return targets, GO_DEFINITION.findall(text)


def _php(path: str, text: str, files: set[str]) -> tuple[set[str], list[str]]:
    targets = set()
    for name in PHP_USE.findall(text):
        tail = name.replace("\\", "/") + ".php"
        match = next((f for f in files if f.endswith("/" + tail) or f == tail), None)
        if match is not None and match != path:
            targets.add(match)
    for spec in PHP_INCLUDE.findall(text):
        target = _resolve_relative(path, spec if spec.startswith(".") else "./" + spec, files, ())
        if target is not None and target != path:
            targets.add(target)
    return targets, PHP_DEFINITION.findall(text)


def _ruby(path: str, text: str, files: set[str]) -> tuple[set[str], list[str]]:
    targets = set()
    for spec in RUBY_REQUIRE.findall(text):
        target = _resolve_relative(path, "./" + spec.lstrip("./"), files, (".rb",))
        if target is not None:
            targets.add(target)
    return targets, RUBY_DEFINITION.findall(text)


def _rust(path: str, text: str, files: set[str]) -> tuple[set[str], list[str]]:
    targets = set()
    folder = posixpath.dirname(path)
    stem = posixpath.splitext(posixpath.basename(path))[0]
    module_dir = folder if stem in ("mod", "lib", "main") else posixpath.join(folder, stem)
    for name in RUST_MOD.findall(text):
        for candidate in (f"{module_dir}/{name}.rs", f"{module_dir}/{name}/mod.rs"):
            candidate = candidate.lstrip("/")
            if candidate in files:
                targets.add(candidate)
    return targets, RUST_DEFINITION.findall(text)


# Building and caching ----------------------------------------------------------------------


def build(repo: Path, commit: str) -> CodeMap:
    """The code map of ``commit`` in ``repo``. Runs git; may take a few seconds."""
    blobs = _blobs(repo, commit)
    texts = _contents(repo, blobs)
    files = set(blobs)
    go_mod = Git(repo).run("show", f"{commit}:go.mod", check=False)
    go_module = ""
    if go_mod.ok:
        found = re.search(r"^module\s+(\S+)", go_mod.stdout, re.M)
        go_module = found.group(1) if found else ""
    code = CodeMap(commit, sorted(files))
    edges: set[tuple[str, str]] = set()
    for path, text in texts.items():
        language = SOURCE_EXTENSIONS[posixpath.splitext(path)[1].lower()]
        if language == "python":
            targets, names = _python(path, text, files)
        elif language == "js":
            targets, names = _js(path, text, files)
        elif language == "go":
            targets, names = _go(path, text, files, go_module)
        elif language == "php":
            targets, names = _php(path, text, files)
        elif language == "ruby":
            targets, names = _ruby(path, text, files)
        else:
            targets, names = _rust(path, text, files)
        edges |= {(path, target) for target in targets}
        if names:
            code.definitions[path] = list(dict.fromkeys(names))[:MAX_DEFINITIONS]
    code.edges = sorted(edges)
    return code


def load_or_build(repo: Path, commit: str, cache_dir: Path) -> CodeMap:
    """The cached code map for ``commit``, built on first use. Older maps are removed."""
    cache = cache_dir / f"codemap-{commit}.json"
    if cache.is_file():
        try:
            return CodeMap.from_json(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            pass
    code = build(repo, commit)
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Two runs may build the same map at once: each writes its own file, the last rename wins.
    tmp = cache.with_name(f"{cache.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(code.to_json(), encoding="utf-8")
    tmp.replace(cache)
    for old in cache_dir.glob("codemap-*.json"):
        if old != cache:
            old.unlink(missing_ok=True)
    return code


# Ranking -------------------------------------------------------------------------------------


def pagerank(
    code: CodeMap,
    seeds: Mapping[str, float] | None = None,
    *,
    damping: float = 0.85,
    rounds: int = 40,
) -> dict[str, float]:
    """PageRank over the import graph, personalised toward ``seeds`` (file to weight).
    Without seeds every file starts equal. Scores sum to 1."""
    files = code.files
    if not files:
        return {}
    index = {path: i for i, path in enumerate(files)}
    outgoing: list[list[int]] = [[] for _ in files]
    for source, target in code.edges:
        if source in index and target in index:
            outgoing[index[source]].append(index[target])
    weights = [float((seeds or {}).get(path, 0.0)) for path in files]
    total = sum(weights)
    personal = [w / total for w in weights] if total > 0 else [1 / len(files)] * len(files)
    rank = list(personal)
    for _ in range(rounds):
        nxt = [(1 - damping) * p for p in personal]
        dangling = 0.0
        for i, targets in enumerate(outgoing):
            if targets:
                share = damping * rank[i] / len(targets)
                for j in targets:
                    nxt[j] += share
            else:
                dangling += damping * rank[i]
        for i, p in enumerate(personal):
            nxt[i] += dangling * p
        rank = nxt
    return {path: rank[i] for i, path in enumerate(files)}


WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]{2,}")


def seeds_for(code: CodeMap, text: str) -> dict[str, float]:
    """Files a card is probably about: named by path or file name, or defining a name the
    card mentions."""
    words = set(WORD.findall(text))
    lowered = {w.lower().strip("./") for w in words}
    seeds: dict[str, float] = {}
    for path in code.files:
        name = posixpath.basename(path).lower()
        stem = posixpath.splitext(name)[0]
        if path.lower() in lowered:
            seeds[path] = seeds.get(path, 0) + 5
        elif name in lowered or (len(stem) > 3 and stem in lowered):
            seeds[path] = seeds.get(path, 0) + 2
    for path, names in code.definitions.items():
        hits = sum(1 for n in names if n in words and len(n) > 3)
        if hits:
            seeds[path] = seeds.get(path, 0) + hits
    return seeds
