"""The mutation sweep and seeded draw described in ../PROTOCOL.md.

    python sweep.py RICH_CLONE OUT_DIR [--workers N]

RICH_CLONE is a git clone of Rich at the protocol's commit. Writes OUT_DIR/mutants.json (every
mutant and its failing tests) and OUT_DIR/drawn.json (the 10 drawn bugs, learning batch first).
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

COMMIT = "3a109ccdffe5b2d0e30684a304fbd388e99ce57b"
SEED = 20261113
DRAW = 26  # Addendum 1 (was 10)
LEARNING = 6  # Addendum 1 (was 4)
MIN_FAILING, MAX_FAILING = 1, 6
TIMEOUT = 180
EXCLUDED = {
    "rich/_emoji_codes.py", "rich/_palettes.py", "rich/_spinners.py", "rich/_win32_console.py",
    "rich/jupyter.py", "rich/__main__.py", "rich/diagnose.py", "rich/_export_format.py",
    "rich/default_styles.py", "rich/themes.py", "rich/logging.py", "rich/traceback.py",
    "rich/_inspect.py",
}  # fmt: skip
FLIP = {ast.Lt: ("<", "<="), ast.LtE: ("<=", "<"), ast.Gt: (">", ">="), ast.GtE: (">=", ">")}


def modules(root: Path) -> list[str]:
    found = []
    for path in sorted((root / "rich").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel in EXCLUDED or rel.startswith("rich/_unicode_data/") or "/_windows" in rel:
            continue
        found.append(rel)
    return found


def mutants_of(root: Path, rel: str) -> list[dict]:
    """Every mutant of one module as byte edits: (start, end, replacement)."""
    data = (root / rel).read_bytes()
    starts = [0]
    for line in data.split(b"\n")[:-1]:
        starts.append(starts[-1] + len(line) + 1)

    def offset(line: int, col: int) -> int:
        return starts[line - 1] + col

    out = []
    for node in ast.walk(ast.parse(data)):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for i, op in enumerate(node.ops):
                if type(op) not in FLIP:
                    continue
                old, new = FLIP[type(op)]
                left, right = operands[i], operands[i + 1]
                lo = offset(left.end_lineno, left.end_col_offset)
                hi = offset(right.lineno, right.col_offset)
                gap = data[lo:hi].decode("utf-8")
                match = re.search(re.escape(old) + r"(?![=<>])", gap)
                if match is None:
                    continue
                start = lo + len(gap[: match.start()].encode("utf-8"))
                out.append(
                    {"module": rel, "line": node.lineno, "col": node.col_offset + i,
                     "kind": f"{old} to {new}", "start": start, "end": start + len(old.encode()),
                     "replacement": new}
                )  # fmt: skip
        elif (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Add | ast.Sub)
            and isinstance(node.right, ast.Constant)
            and node.right.value == 1
            and type(node.right.value) is int
        ):
            left = node.left
            start = offset(node.lineno, node.col_offset)
            end = offset(node.end_lineno, node.end_col_offset)
            text = data[offset(left.lineno, left.col_offset) : offset(left.end_lineno, left.end_col_offset)]
            sign = "+" if isinstance(node.op, ast.Add) else "-"
            out.append(
                {"module": rel, "line": node.lineno, "col": node.col_offset,
                 "kind": f"drop {sign} 1", "start": start, "end": end,
                 "replacement": text.decode("utf-8")}
            )  # fmt: skip
    return sorted(out, key=lambda m: (m["module"], m["line"], m["col"], m["start"]))


def run_suite(tree: Path) -> dict:
    report = tree / "sweep-report.xml"
    report.unlink(missing_ok=True)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests",
             f"--junitxml={report}"],
            cwd=tree, capture_output=True, text=True, timeout=TIMEOUT,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        return {"timeout": True, "failing": []}
    if not report.exists() or proc.returncode in (2, 3, 4):
        return {"error": proc.returncode, "failing": []}
    failing = []
    for case in ET.parse(report).iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            failing.append(f"{case.get('classname')}::{case.get('name')}")
    return {"failing": sorted(failing)}


def worker_tree(root: Path, work: Path, index: int) -> Path:
    tree = work / f"w{index}"
    if not tree.exists():
        subprocess.run(["git", "worktree", "add", "-q", "--detach", str(tree), COMMIT], cwd=root, check=True)
    return tree


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("rich", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    root, out = args.rich.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    if head != COMMIT:
        sys.exit(f"Rich is at {head}, the protocol needs {COMMIT}")
    mutants = [m for rel in modules(root) for m in mutants_of(root, rel)]
    print(f"{len(mutants)} mutants in {len(modules(root))} modules", flush=True)

    work = out / "trees"
    subprocess.run(["git", "worktree", "prune"], cwd=root)
    trees = [worker_tree(root, work, i) for i in range(args.workers)]
    if run_suite(trees[0])["failing"]:
        sys.exit("Rich's suite does not pass unchanged")

    def mutate(job: tuple[int, dict]) -> dict:
        index, mutant = job
        tree = trees[index % len(trees)]
        path = tree / mutant["module"]
        original = path.read_bytes()
        mutated = original[: mutant["start"]] + mutant["replacement"].encode() + original[mutant["end"] :]
        path.write_bytes(mutated)
        try:
            result = run_suite(tree)
        finally:
            path.write_bytes(original)
        if index % 25 == 0:
            print(f"  {index}/{len(mutants)}", flush=True)
        return {**mutant, **result}

    # Each worker owns one tree: jobs are split so that a tree runs one mutant at a time.
    results: list[dict] = [{}] * len(mutants)
    lanes = [[(i, m) for i, m in enumerate(mutants) if i % len(trees) == lane] for lane in range(len(trees))]

    def lane_run(lane: list[tuple[int, dict]]) -> None:
        for job in lane:
            results[job[0]] = mutate(job)

    with ThreadPoolExecutor(len(trees)) as pool:
        list(pool.map(lane_run, lanes))
    (out / "mutants.json").write_text(json.dumps(results, indent=1), encoding="utf-8")

    qualifying = [
        m for m in results
        if not m.get("timeout") and "error" not in m and MIN_FAILING <= len(m["failing"]) <= MAX_FAILING
    ]  # fmt: skip
    random.Random(SEED).shuffle(qualifying)
    drawn, used = [], set()
    for mutant in qualifying:
        if mutant["module"] in used:
            continue
        drawn.append(mutant)
        used.add(mutant["module"])
        if len(drawn) == DRAW:
            break
    for i, mutant in enumerate(drawn):
        mutant["batch"] = "learning" if i < LEARNING else "test"
    # Addendum 1: the lesson case is the next mutant in the same walk, from an unused module.
    for mutant in qualifying[qualifying.index(drawn[-1]) + 1 :]:
        if mutant["module"] not in used:
            drawn.append({**mutant, "batch": "lesson"})
            break
    (out / "drawn.json").write_text(json.dumps(drawn, indent=1), encoding="utf-8")
    print(f"{len(qualifying)} qualifying; drew {len(drawn)}")
    for mutant in drawn:
        print(f"  {mutant['batch']:8} {mutant['module']}:{mutant['line']} {mutant['kind']} "
              f"({len(mutant['failing'])} failing)")  # fmt: skip
    for tree in trees:
        subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=root)
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
