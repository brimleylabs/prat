"""The harder cases of ADDENDUM-4.md: three bugs to a ticket.

    python build_gnarly.py draw RICH_CLONE              # writes ../sweep/gnarly-drawn.json
    python build_gnarly.py describe RICH_CLONE           # what each drawn bug's tests report
    python build_gnarly.py build RICH_CLONE SNAPSHOT     # cases/gnarly/ and the snapshot

``draw`` walks the unused qualifying mutants in the seeded order, fills cases of three (no two
bugs of a case in the same module or sharing a failing test) and verifies each case as it is
filled, discarding one that does not verify. ``build`` writes the cases from the draw and
``../gnarly-cards.json``, and the snapshot with all the drawn bugs that knowledge is built from.
"""

from __future__ import annotations

import json
import random
import shutil
import sys
from pathlib import Path

from build_cases import (
    COMMIT,
    REPOSITORY,
    SUITE,
    apply,
    catching,
    describe,
    force_rmtree,
    mutant_id,
    project_file,
    run,
    suite,
    without_tests,
)

HERE = Path(__file__).resolve().parents[1]
SEED = 20261114
CASES = 6
SIZE = 3
DRAWN = HERE / "sweep" / "gnarly-drawn.json"
CARDS = HERE / "gnarly-cards.json"
OUT = HERE / "cases" / "gnarly"
TAIL_CRITERIA = "- the existing tests still pass\n- a test covers each fix"


def pool() -> list[dict]:
    mutants = json.loads((HERE / "sweep" / "mutants.json").read_text(encoding="utf-8"))
    used = set(json.loads((HERE / "cards.json").read_text(encoding="utf-8")))
    return [
        m
        for m in mutants
        if isinstance(m["failing"], list) and 1 <= len(m["failing"]) <= 6 and mutant_id(m) not in used
    ]


def checkout(rich: Path) -> Path:
    tree = rich.parent / "gnarly-tree"
    run(["git", "worktree", "prune"], rich)
    if tree.exists():
        run(["git", "worktree", "remove", "--force", str(tree)], rich)
    run(["git", "worktree", "add", "-q", "--detach", str(tree), COMMIT], rich)
    return tree


def mutated_modules(tree: Path, bugs: list[dict]) -> dict[str, bytes]:
    """Each touched module with its bugs applied (last first, so offsets hold), LF endings."""
    by_module: dict[str, list[dict]] = {}
    for m in bugs:
        by_module.setdefault(m["module"], []).append(m)
    result = {}
    for module, ms in by_module.items():
        data = (tree / module).read_bytes()
        for m in sorted(ms, key=lambda m: m["start"], reverse=True):
            data = apply(data, m)
        result[module] = data.replace(b"\r\n", b"\n")
    return result


def removals(bugs: list[dict]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for m in bugs:
        for path, names in catching(m).items():
            found.setdefault(path, set()).update(names)
    return found


def verify(tree: Path, bugs: list[dict]) -> dict:
    """Visible tests pass with the bugs; the hidden check fails with them and passes without."""
    originals = {p: (tree / p).read_bytes() for p in {m["module"] for m in bugs} | set(removals(bugs))}
    try:
        for module, data in mutated_modules(tree, bugs).items():
            (tree / module).write_bytes(data)
        for path, names in removals(bugs).items():
            source = originals[path].decode("utf-8")
            (tree / path).write_text(without_tests(source, names), encoding="utf-8", newline="")
        visible = suite(tree)
        for path in removals(bugs):
            (tree / path).write_bytes(originals[path])
        hidden_bug = suite(tree)
    finally:
        for path, data in originals.items():
            (tree / path).write_bytes(data)
    hidden_fixed = suite(tree)
    ok = visible == 0 and hidden_bug != 0 and hidden_fixed == 0
    return {"ok": ok, "visible": visible, "hidden_with_bugs": hidden_bug, "hidden_fixed": hidden_fixed}


def draw(rich: Path) -> None:
    order = pool()
    random.Random(SEED).shuffle(order)
    tree = checkout(rich)
    cases: list[dict] = []
    current: list[dict] = []
    log = []
    for m in order:
        if len(cases) == CASES:
            break
        tests = set(m["failing"])
        if any(o["module"] == m["module"] or tests & set(o["failing"]) for o in current):
            log.append({"mutant": mutant_id(m), "skipped": "same module or shared test"})
            continue
        current.append(m)
        if len(current) == SIZE:
            check = verify(tree, current)
            log.append({"case": [mutant_id(b) for b in current], **check})
            print(json.dumps(log[-1]), flush=True)
            if check["ok"]:
                cases.append({"case": len(cases) + 1, "bugs": current})
            current = []
    run(["git", "worktree", "remove", "--force", str(tree)], rich)
    DRAWN.write_text(json.dumps({"seed": SEED, "cases": cases, "walk": log}, indent=1), encoding="utf-8")
    print(f"{len(cases)} cases drawn")


def describe_draw(rich: Path) -> None:
    drawn = json.loads(DRAWN.read_text(encoding="utf-8"))
    flat = [dict(m, batch=f"case {c['case']}") for c in drawn["cases"] for m in c["bugs"]]
    tmp = HERE / "sweep" / "gnarly-describe.json"
    tmp.write_text(json.dumps(flat), encoding="utf-8")
    try:
        describe(rich, tmp)
    finally:
        tmp.unlink()


def build(rich: Path, snapshot: Path) -> None:
    drawn = json.loads(DRAWN.read_text(encoding="utf-8"))["cases"]
    cards = json.loads(CARDS.read_text(encoding="utf-8"))
    tree = checkout(rich)
    report = []
    for case in drawn:
        card = cards[str(case["case"])]
        bugs = case["bugs"]
        folder = OUT / card["name"]
        if folder.exists():
            shutil.rmtree(folder)
        for module, data in mutated_modules(tree, bugs).items():
            (folder / "repo" / module).parent.mkdir(parents=True, exist_ok=True)
            (folder / "repo" / module).write_bytes(data)
        for path, names in removals(bugs).items():
            source = (tree / path).read_bytes().decode("utf-8").replace("\r\n", "\n")
            for side, text in (("hidden", source), ("repo", without_tests(source, names))):
                (folder / side / path).parent.mkdir(parents=True, exist_ok=True)
                (folder / side / path).write_text(text, encoding="utf-8", newline="")
        (folder / "project.toml").write_text(project_file(False), encoding="utf-8", newline="\n")
        goal = card["intro"] + "\n\n" + "\n".join(
            f"{i}. {s}" for i, s in enumerate(card["symptoms"], 1)
        )
        criteria = "\n".join(f"- {c}" for c in card["criteria"]) + "\n" + TAIL_CRITERIA
        ids = ", ".join(mutant_id(m) for m in bugs)
        (folder / "case.toml").write_text(
            f"# Rich {COMMIT[:12]}, mutants {ids} (gnarly case {case['case']}; see ADDENDUM-4.md).\n"
            f"title = {json.dumps(card['title'], ensure_ascii=False)}\n"
            f"goal = {json.dumps(goal, ensure_ascii=False)}\n"
            f"acceptance_criteria = {json.dumps(criteria, ensure_ascii=False)}\n"
            f"complexity = \"medium\"\nmax_attempts = 3\n\n"
            f"[source]\nrepository = \"{REPOSITORY}\"\ncommit = \"{COMMIT}\"\n\n"
            f"[check]\ncommand = {json.dumps(SUITE)}\ntimeout_seconds = 300\n",
            encoding="utf-8", newline="\n",
        )  # fmt: skip
        report.append({"case": card["name"], **verify(tree, bugs)})
        print(json.dumps(report[-1]), flush=True)
    (OUT / "verification.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    # The snapshot: Rich with every drawn bug and without every catching test.
    every = [m for case in drawn for m in case["bugs"]]
    if snapshot.exists():
        force_rmtree(snapshot)
    from prat.evals.runner import export_tree

    export_tree(rich, COMMIT, snapshot)
    for module, data in mutated_modules(tree, every).items():
        (snapshot / module).write_bytes(data)
    for path, names in removals(every).items():
        source = (snapshot / path).read_text(encoding="utf-8")
        (snapshot / path).write_text(without_tests(source, names), encoding="utf-8", newline="")
    (snapshot / ".prat").mkdir()
    (snapshot / ".prat" / "project.toml").write_text(project_file(False), encoding="utf-8", newline="\n")
    for args in (["init", "-q", "--initial-branch", "main"], ["config", "core.autocrlf", "false"],
                 ["config", "user.name", "PRAT evaluation"], ["config", "user.email", "eval@prat.invalid"],
                 ["add", "-A"], ["commit", "-q", "--no-verify", "-m", "Rich"]):  # fmt: skip
        run(["git", *args], snapshot)
    print(f"snapshot: {len(every)} bugs, visible suite exit {suite(snapshot)}")
    run(["git", "worktree", "remove", "--force", str(tree)], rich)


if __name__ == "__main__":
    command, rich, *rest = sys.argv[1:]
    if command == "draw":
        draw(Path(rich))
    elif command == "describe":
        describe_draw(Path(rich))
    elif command == "build":
        build(Path(rich), Path(rest[0]))
    else:
        sys.exit(__doc__)
