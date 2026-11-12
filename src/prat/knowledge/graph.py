"""The knowledge graph of a project, as plain data for the web page and ``prat knowledge graph``.

Nodes are entries, the repository files they are about and the project itself (for entries
about all of it); with the code map, the files that
matter most to the code (by PageRank) and the imports between shown files are added, so the
graph shows where knowledge sits in the code and where it is missing. Edges are an entry being
about a file, links between entries (related, refines, contradicts), an entry superseded by
another, and imports.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prat.git import Git
from prat.knowledge.codemap import CodeMap, load_or_build, pagerank
from prat.knowledge.entries import Entry, KnowledgeBase
from prat.knowledge.inventory import Inventory

MAX_FILES = 250
TOP_FILES = 40
MAX_INVENTORY_FILES = 1500
MAX_CALL_EDGES = 800


@dataclass
class Graph:
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    commit: str = ""
    entries: int = 0
    files: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "commit": self.commit,
            "counts": {"entries": self.entries, "files": self.files},
        }


def code_map_for(kb: KnowledgeBase, agent: Path, branch: str) -> CodeMap | None:
    """The code map of the default branch in PRAT's clone, if the clone exists."""
    if not agent.is_dir():
        return None
    shown = Git(agent).run("rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}", check=False)
    head = shown.stdout.strip() if shown.ok else ""
    return load_or_build(agent, head, kb.cache_dir()) if head else None


def entry_node(entry: Entry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "type": "entry",
        "kind": entry.kind,
        "label": entry.summary,
        "status": entry.status,
        "confidence": entry.confidence,
        "author": entry.author,
        "helped": entry.helped,
        "hurt": entry.hurt,
        "paths": entry.paths,
    }


def build_graph(
    entries: Iterable[Entry],
    code: CodeMap | None = None,
    *,
    include_inactive: bool = False,
    project: str = "",
    inventory: Inventory | None = None,
) -> Graph:
    """``project`` names a node that entries about the whole project attach to. With an
    inventory (from a deep scan) the graph is the whole project: modules, every readable file,
    imports, calls and files that change together, with entries on what they are about."""
    if inventory is not None:
        return _inventory_graph(
            entries, inventory, include_inactive=include_inactive, project=project
        )
    graph = Graph(commit=code.commit if code else "")
    shown = [e for e in entries if include_inactive or e.active]
    ids = {e.id for e in shown}
    known = set(code.files) if code else set()
    ranks = pagerank(code) if code else {}
    files: dict[str, dict[str, Any]] = {}

    def file_node(path: str) -> None:
        if path in files or len(files) >= MAX_FILES:
            return
        files[path] = {
            "id": f"file:{path}",
            "type": "file",
            "label": path,
            "rank": round(ranks.get(path, 0.0), 6),
            "missing": bool(code) and path not in known and not _folder(path, known),
        }

    whole = [e for e in shown if not e.paths]
    if project and whole:
        graph.nodes.append({"id": "project", "type": "project", "label": project})
    for entry in shown:
        graph.nodes.append(entry_node(entry))
        if project and not entry.paths:
            graph.edges.append({"source": entry.id, "target": "project", "relation": "about"})
        for path in entry.paths:
            file_node(path)
            if path in files:
                graph.edges.append(
                    {"source": entry.id, "target": f"file:{path}", "relation": "about"}
                )
        for link in entry.links:
            if link.to in ids:
                graph.edges.append(
                    {"source": entry.id, "target": link.to, "relation": link.relation}
                )
        if entry.superseded_by and entry.superseded_by in ids:
            graph.edges.append(
                {"source": entry.id, "target": entry.superseded_by, "relation": "superseded_by"}
            )
    covered = set(files)
    # The files the code leans on most, so gaps in what PRAT knows are visible.
    for path, _ in sorted(ranks.items(), key=lambda item: item[1], reverse=True)[:TOP_FILES]:
        file_node(path)
    for node in files.values():
        node["known"] = node["label"] in covered
    if code:
        for source, target in code.edges:
            if source in files and target in files:
                graph.edges.append(
                    {"source": f"file:{source}", "target": f"file:{target}", "relation": "imports"}
                )
    graph.nodes += list(files.values())
    graph.entries = len(shown)
    graph.files = len(files)
    return graph


def _folder(path: str, known: set[str]) -> bool:
    prefix = path.rstrip("/") + "/"
    return any(file.startswith(prefix) for file in known)


def _inventory_graph(
    entries: Iterable[Entry], inv: Inventory, *, include_inactive: bool, project: str
) -> Graph:
    graph = Graph(commit=inv.commit)
    shown = [e for e in entries if include_inactive or e.active]
    ids = {e.id for e in shown}
    churn = {h["path"]: h["commits"] for h in inv.hotspots}
    root = "project"
    graph.nodes.append({"id": root, "type": "project", "label": project or "project"})
    modules: dict[str, dict[str, Any]] = {}
    for m in inv.modules:
        modules[m.name] = {
            "id": f"module:{m.name}",
            "type": "module",
            "label": m.name,
            "files": m.data_count if m.data else len(m.files),
            "data": m.data,
            "known": False,
        }
        graph.edges.append({"source": f"module:{m.name}", "target": root, "relation": "in"})
    files: dict[str, dict[str, Any]] = {}
    for f in inv.readable[:MAX_INVENTORY_FILES]:
        files[f.path] = {
            "id": f"file:{f.path}",
            "type": "file",
            "label": f.path,
            "lines": f.lines,
            "commits": churn.get(f.path, 0),
            "symbols": inv.symbols.get(f.path, 0),
            "kind": f.kind,
            "known": False,
        }
        if f.module in modules:
            graph.edges.append(
                {"source": f"file:{f.path}", "target": f"module:{f.module}", "relation": "in"}
            )
    folders = {m.folder: name for name, m in ((m.name, m) for m in inv.modules)}
    for entry in shown:
        graph.nodes.append(entry_node(entry))
        targets: list[str] = []
        for path in entry.paths:
            clean = path.rstrip("/")
            if clean in files:
                files[clean]["known"] = True
                targets.append(f"file:{clean}")
            elif clean in folders:
                modules[folders[clean]]["known"] = True
                targets.append(f"module:{folders[clean]}")
            else:
                under = [
                    module_name
                    for folder, module_name in folders.items()
                    if folder.startswith(clean + "/") or clean.startswith(folder + "/")
                ]
                for module_name in under[:3]:
                    modules[module_name]["known"] = True
                    targets.append(f"module:{module_name}")
        for target in dict.fromkeys(targets or [root]):
            graph.edges.append({"source": entry.id, "target": target, "relation": "about"})
        for link in entry.links:
            if link.to in ids:
                graph.edges.append(
                    {"source": entry.id, "target": link.to, "relation": link.relation}
                )
        if entry.superseded_by and entry.superseded_by in ids:
            graph.edges.append(
                {"source": entry.id, "target": entry.superseded_by, "relation": "superseded_by"}
            )
    for a, b in inv.imports:
        if a in files and b in files:
            graph.edges.append(
                {"source": f"file:{a}", "target": f"file:{b}", "relation": "imports"}
            )
    for a, b, relation, count in inv.calls[:MAX_CALL_EDGES]:
        if a in files and b in files:
            graph.edges.append(
                {
                    "source": f"file:{a}",
                    "target": f"file:{b}",
                    "relation": "calls",
                    "count": count,
                    "kind": relation,
                }
            )
    for pair in inv.cochange:
        if pair["a"] in files and pair["b"] in files:
            graph.edges.append(
                {
                    "source": f"file:{pair['a']}",
                    "target": f"file:{pair['b']}",
                    "relation": "changes_with",
                    "count": pair["together"],
                }
            )
    graph.nodes += list(modules.values()) + list(files.values())
    graph.entries = len(shown)
    graph.files = len(files)
    return graph
