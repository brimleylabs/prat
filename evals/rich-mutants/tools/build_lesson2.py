"""The second lesson case (see ../ADDENDUM-3.md), derived from the first.

    python build_lesson2.py                       # writes cases/lesson2/
    python build_lesson2.py SNAPSHOT DEST          # also: the lesson snapshot, changed the same way

Same bug, same card, same Rich commit. What changes is the rule the work is judged by: no
changelog gate and no changelog checker in the repository; instead the team wants every bug
fix to come with a regression test marked ``@pytest.mark.regression`` that fails without the
fix. That rule is written down nowhere in the repository. The hidden check enforces it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SOURCE = HERE / "cases" / "lesson" / "svg-export-width"
DEST = HERE / "cases" / "lesson2" / "svg-export-width-regression"

PROJECT = """\
name = "rich"
default_branch = "main"
autonomy = "medium"
notes = "Rich, a Python library for rich text and formatting in the terminal. The package is rich/, the tests are in tests/. Run the tests with: python -m pytest -q -p no:cacheprovider tests (about 1,000 tests, a few seconds)."

[commands]
test = ["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]

[verify]
gates = ["test"]
timeout_seconds = 300
"""

CHECK = '''\
"""Hidden check for the lesson2 case: the fix, and the team's regression-test rule.

1. The tests marked regression (the agent's own) pass.
2. They fail once the bug is put back (mutant_console.py over rich/console.py).
3. Rich's own tests pass, with the tests that catch the bug put back.
"""

import shutil
import subprocess
import sys
from pathlib import Path


def pytest(*args):
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args]
    return subprocess.run(command).returncode


code = pytest("-m", "regression", "tests")
if code != 0:
    print(f"RULE: no passing test marked regression (pytest exit {code})")
    sys.exit(1)
module = Path("rich/console.py")
fixed = module.read_bytes()
shutil.copyfile("mutant_console.py", module)
code = pytest("-m", "regression", "tests")
module.write_bytes(fixed)
if code == 0:
    print("RULE: the tests marked regression still pass with the bug put back")
    sys.exit(1)
shutil.copyfile("hidden_tests/test_console.py", "tests/test_console.py")
sys.exit(pytest("tests"))
'''

CASE_HEADER = (
    "# Rich 3a109ccdffe5, mutant rich/console.py:2581:27:drop - 1 (lesson2; see ADDENDUM-3.md).\n"
)


def build_case() -> None:
    if DEST.exists():
        shutil.rmtree(DEST)
    (DEST / "repo").mkdir(parents=True)
    shutil.copytree(SOURCE / "repo" / "rich", DEST / "repo" / "rich")
    shutil.copytree(SOURCE / "repo" / "tests", DEST / "repo" / "tests")
    hidden = DEST / "hidden"
    (hidden / "hidden_tests").mkdir(parents=True)
    shutil.copyfile(SOURCE / "hidden" / "tests" / "test_console.py", hidden / "hidden_tests" / "test_console.py")
    shutil.copyfile(SOURCE / "repo" / "rich" / "console.py", hidden / "mutant_console.py")
    (hidden / "check_regression.py").write_bytes(CHECK.encode())
    (DEST / "project.toml").write_bytes(PROJECT.encode())
    case = (SOURCE / "case.toml").read_text(encoding="utf-8").splitlines(keepends=True)
    body = [line for line in case[1:] if not line.startswith("command = ")]
    text = CASE_HEADER + "".join(body).replace(
        "[check]\n", '[check]\ncommand = ["{python}", "check_regression.py"]\n'
    )
    (DEST / "case.toml").write_bytes(text.encode())


def build_snapshot(snapshot: Path, dest: Path) -> None:
    """The lesson snapshot without the changelog checker and with this case's project file."""
    if dest.exists():
        sys.exit(f"{dest} exists")
    subprocess.run(["git", "clone", "-q", str(snapshot), str(dest)], check=True)
    shutil.rmtree(dest / ".git", onexc=lambda f, p, _: (Path(p).chmod(0o666), f(p)))
    (dest / "tools" / "check_changelog.py").unlink()
    (dest / ".prat" / "project.toml").write_bytes(PROJECT.encode())
    for args in (["init", "-q", "-b", "main"], ["add", "-A"]):
        subprocess.run(["git", *args], cwd=dest, check=True)
    subprocess.run(
        ["git", "-c", "user.name=PRAT benchmark", "-c", "user.email=bench@example.invalid",
         "commit", "-q", "--no-verify", "-m", "Rich 3a109ccdffe5 with the lesson2 case's bug"],
        cwd=dest, check=True,
    )  # fmt: skip


if __name__ == "__main__":
    build_case()
    if len(sys.argv) == 3:
        build_snapshot(Path(sys.argv[1]), Path(sys.argv[2]))
