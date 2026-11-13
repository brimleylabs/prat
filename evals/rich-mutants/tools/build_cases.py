"""Builds the benchmark's cases and snapshots from the draw (see ../PROTOCOL.md, ADDENDUM-1.md).

    python build_cases.py describe RICH_CLONE DRAWN_JSON
    python build_cases.py build RICH_CLONE DRAWN_JSON CARDS_JSON OUT_DIR SNAPSHOTS_DIR

``describe`` prints each drawn bug: the changed line and what its failing tests report, which
is what each card is written from. ``build`` writes the cases (OUT_DIR/test, /learning,
/lesson), the snapshot repositories that knowledge is built from (SNAPSHOTS_DIR/main with every
learning and test bug, SNAPSHOTS_DIR/lesson with the lesson bug and the changelog rule), and
verifies every case: the visible tests pass with the bug, the hidden check fails with it and
passes once the original code is back.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

COMMIT = "3a109ccdffe5b2d0e30684a304fbd388e99ce57b"
REPOSITORY = "https://github.com/Textualize/rich.git"
CRITERIA = "- the bug described above is fixed\n- the existing tests still pass\n- a test covers the fix"
NOTES = (
    "Rich, a Python library for rich text and formatting in the terminal. The package is rich/, "
    "the tests are in tests/. Run the tests with: python -m pytest -q -p no:cacheprovider tests "
    "(about 1,000 tests, a few seconds)."
)
PROJECT = """name = "rich"
default_branch = "main"
autonomy = "medium"
notes = {notes}

[commands]
test = ["{{python}}", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
{extra_commands}
[verify]
gates = {gates}
timeout_seconds = 300
"""
SUITE = ["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]

CHANGELOG_CHECK = '''"""Rich's changelog rule (CONTRIBUTING.md): every change under rich/ is described in
CHANGELOG.md, in a new entry under "## Unreleased".

    python tools/check_changelog.py [BASE_CHANGELOG]

Compares CHANGELOG.md with BASE_CHANGELOG, or with CHANGELOG.md on the branch this work
started from (git merge-base with main).
"""

import subprocess
import sys
from pathlib import Path


def unreleased_entries(text):
    entries, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.strip().lower() == "## unreleased"
            continue
        if inside and line.strip().startswith("- "):
            entries.append(line.strip())
    return entries


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8")


def main():
    if len(sys.argv) > 1:
        base = Path(sys.argv[1]).read_text(encoding="utf-8")
        changed_code = True
    else:
        fork = git("merge-base", "HEAD", "main").stdout.strip() or "HEAD"
        base = git("show", f"{fork}:CHANGELOG.md").stdout
        changed = git("diff", "--name-only", fork, "--", "rich/").stdout.split()
        dirty = git("status", "--porcelain", "--", "rich/").stdout.split()
        changed_code = bool(changed or dirty)
    if not changed_code:
        print("no change under rich/: nothing to describe")
        return 0
    now = unreleased_entries(Path("CHANGELOG.md").read_text(encoding="utf-8"))
    before = unreleased_entries(base)
    if len([e for e in now if e not in before]) == 0:
        print("CHANGELOG.md: this change under rich/ has no new entry under ## Unreleased.")
        print("Rich asks for every change to be described there (see CONTRIBUTING.md).")
        return 1
    print("changelog ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def force_rmtree(path: Path) -> None:
    """Remove a folder, including git's read-only files on Windows."""

    def retry(func, target, _exc):
        Path(target).chmod(0o700)
        func(target)

    shutil.rmtree(path, onexc=retry)


def run(args, cwd, **kw):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def mutant_id(m: dict) -> str:
    return f"{m['module']}:{m['line']}:{m['col']}:{m['kind']}"


def apply(data: bytes, m: dict) -> bytes:
    return data[: m["start"]] + m["replacement"].encode() + data[m["end"] :]


def test_path(failing: str) -> tuple[str, str]:
    classname, name = failing.split("::", 1)
    parts = classname.split(".")
    index = next(i for i, p in enumerate(parts) if p.startswith("test_"))
    path = "/".join(parts[: index + 1]) + ".py"
    if not path.startswith("tests/"):
        path = "tests/" + path
    return path, name.split("[")[0]


def catching(m: dict) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for failing in m["failing"]:
        path, name = test_path(failing)
        found.setdefault(path, set()).add(name)
    return found


def without_tests(source: str, names: set[str]) -> str:
    lines = source.splitlines(keepends=True)
    cut = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in names:
            start = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
            cut.append((start, node.end_lineno))
    for start, end in sorted(cut, reverse=True):
        del lines[start:end]
    return "".join(lines)


def suite(tree: Path) -> int:
    return run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], tree).returncode


def describe(rich: Path, drawn_file: Path) -> None:
    drawn = json.loads(drawn_file.read_text(encoding="utf-8"))
    tree = rich.parent / "describe-tree"
    run(["git", "worktree", "prune"], rich)
    if not tree.exists():
        run(["git", "worktree", "add", "-q", "--detach", str(tree), COMMIT], rich)
    for m in drawn:
        path = tree / m["module"]
        original = path.read_bytes()
        line = original.decode().splitlines()[m["line"] - 1].strip()
        path.write_bytes(apply(original, m))
        changed = path.read_bytes().decode().splitlines()[m["line"] - 1].strip()
        tests = sorted({p for p, _ in map(test_path, m["failing"])})
        out = run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x", "--tb=short", *tests], tree).stdout
        path.write_bytes(original)
        print(f"=== [{m['batch']}] {mutant_id(m)}\n    was: {line}\n    now: {changed}")
        print("    failing:", ", ".join(m["failing"]))
        print("\n".join("    | " + l for l in out.splitlines()[-40:]))
    run(["git", "worktree", "remove", "--force", str(tree)], rich)


def project_file(lesson: bool) -> str:
    if lesson:
        extra = 'lint = ["{python}", "tools/check_changelog.py"]\n'
        gates = '["test", "lint"]'
    else:
        extra, gates = "", '["test"]'
    return PROJECT.format(notes=json.dumps(NOTES), extra_commands=extra, gates=gates)


def build(rich: Path, drawn_file: Path, cards_file: Path, out: Path, snapshots: Path) -> None:
    drawn = json.loads(drawn_file.read_text(encoding="utf-8"))
    cards = json.loads(cards_file.read_text(encoding="utf-8"))
    tree = rich.parent / "build-tree"
    run(["git", "worktree", "prune"], rich)
    if tree.exists():
        run(["git", "worktree", "remove", "--force", str(tree)], rich)
    run(["git", "worktree", "add", "-q", "--detach", str(tree), COMMIT], rich)
    original_changelog = (tree / "CHANGELOG.md").read_text(encoding="utf-8")
    report = []
    for m in drawn:
        card = cards[mutant_id(m)]
        folder = out / m["batch"] / card["name"]
        shutil.rmtree(folder, ignore_errors=True)
        module = tree / m["module"]
        original = module.read_bytes()
        mutated = apply(original, m)
        (folder / "repo" / Path(m["module"]).parent).mkdir(parents=True, exist_ok=True)
        # Git checks Rich out with CRLF on Windows; the case keeps Rich's own LF line endings.
        (folder / "repo" / m["module"]).write_bytes(mutated.replace(b"\r\n", b"\n"))
        caught = catching(m)
        for path, names in caught.items():
            source = (tree / path).read_text(encoding="utf-8")
            (folder / "hidden" / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / "hidden" / path).write_text(source, encoding="utf-8", newline="")
            (folder / "repo" / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / "repo" / path).write_text(without_tests(source, names), encoding="utf-8", newline="")
        lesson = m["batch"] == "lesson"
        check = SUITE
        if lesson:
            (folder / "repo" / "tools").mkdir(parents=True, exist_ok=True)
            (folder / "repo" / "tools" / "check_changelog.py").write_text(CHANGELOG_CHECK, encoding="utf-8", newline="")
            (folder / "hidden" / "changelog_base.md").write_text(original_changelog, encoding="utf-8", newline="")
            check = ["sh", "-c", "{python} -m pytest -q -p no:cacheprovider tests && {python} tools/check_changelog.py changelog_base.md"]
        (folder / "project.toml").write_text(project_file(lesson), encoding="utf-8", newline="\n")
        (folder / "case.toml").write_text(
            f"# Rich {COMMIT[:12]}, mutant {mutant_id(m)} ({m['batch']} batch; see PROTOCOL.md).\n"
            f"title = {json.dumps(card['title'], ensure_ascii=False)}\n"
            f"goal = {json.dumps(card['goal'], ensure_ascii=False)}\n"
            f"acceptance_criteria = {json.dumps(CRITERIA)}\ncomplexity = \"low\"\nmax_attempts = 3\n\n"
            f"[source]\nrepository = \"{REPOSITORY}\"\ncommit = \"{COMMIT}\"\n\n"
            f"[check]\ncommand = {json.dumps(check)}\ntimeout_seconds = 300\n",
            encoding="utf-8", newline="\n",
        )  # fmt: skip
        # Verify on the tree: visible passes with the bug; hidden fails with it, passes without.
        for path in caught:
            shutil.copyfile(folder / "repo" / path, tree / path)
        module.write_bytes(mutated)
        visible = suite(tree)
        for path in caught:
            shutil.copyfile(folder / "hidden" / path, tree / path)
        hidden_bug = suite(tree)
        module.write_bytes(original)
        hidden_fixed = suite(tree)
        ok = visible == 0 and hidden_bug != 0 and hidden_fixed == 0
        report.append({"case": card["name"], "batch": m["batch"], "ok": ok, "visible": visible,
                       "hidden_with_bug": hidden_bug, "hidden_fixed": hidden_fixed})  # fmt: skip
        print(json.dumps(report[-1]), flush=True)
    run(["git", "worktree", "remove", "--force", str(tree)], rich)
    (out / "verification.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    def folder_of(m: dict) -> Path:
        return out / m["batch"] / cards[mutant_id(m)]["name"]

    main = [m for m in drawn if m["batch"] != "lesson"]
    make_snapshot(rich, main, snapshots / "main", lesson=False, folder_of=folder_of)
    lesson = [m for m in drawn if m["batch"] == "lesson"]
    make_snapshot(rich, lesson, snapshots / "lesson", lesson=True, folder_of=folder_of)


def make_snapshot(rich: Path, mutants: list[dict], dest: Path, *, lesson: bool, folder_of) -> None:
    """Rich as it ships (from git's objects, LF), with these cases' bugs and without the tests
    that catch any of them, as one commit."""
    from prat.evals.runner import export_tree

    if dest.exists():
        force_rmtree(dest)
    export_tree(rich, COMMIT, dest)
    removals: dict[str, set[str]] = {}
    for m in mutants:
        shutil.copyfile(folder_of(m) / "repo" / m["module"], dest / m["module"])
        for test, names in catching(m).items():
            removals.setdefault(test, set()).update(names)
    for test, names in removals.items():
        path = dest / test
        path.write_text(without_tests(path.read_text(encoding="utf-8"), names), encoding="utf-8", newline="")
    (dest / ".prat").mkdir()
    (dest / ".prat" / "project.toml").write_text(project_file(lesson), encoding="utf-8", newline="\n")
    if lesson:
        (dest / "tools").mkdir(exist_ok=True)
        (dest / "tools" / "check_changelog.py").write_text(CHANGELOG_CHECK, encoding="utf-8", newline="")
    run(["git", "init", "-q", "--initial-branch", "main"], dest)
    run(["git", "config", "core.autocrlf", "false"], dest)
    run(["git", "config", "user.name", "PRAT evaluation"], dest)
    run(["git", "config", "user.email", "eval@prat.invalid"], dest)
    run(["git", "add", "-A"], dest)
    run(["git", "commit", "-q", "--no-verify", "-m", "Rich"], dest)
    print(f"snapshot {dest.name}: {len(mutants)} bug(s), visible suite exit {suite(dest)}", flush=True)


def main() -> None:
    command, *args = sys.argv[1:]
    paths = [Path(a).resolve() for a in args]
    if command == "describe":
        describe(*paths)
    elif command == "build":
        build(*paths)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
