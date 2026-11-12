"""The trusted invariant run: the copy from before a change judges the candidate."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from conftest import git, make_repo
from prat.improve.invariants import (
    INVARIANT_DIR,
    INVARIANTS,
    export_trusted,
    run_invariants,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]

FLAG_INVARIANT = """\
import os
from pathlib import Path


def test_inv_flag_is_ok():
    root = Path(os.environ["PRAT_CANDIDATE_ROOT"])
    assert (root / "flag.txt").read_text(encoding="utf-8") == "ok"


def test_inv_plugins_are_not_autoloaded():
    assert os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
"""


def function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


# The list ----------------------------------------------------------------------------


def test_every_invariant_has_tests_that_exist() -> None:
    folder = REPO_ROOT / INVARIANT_DIR
    assert len({inv.key for inv in INVARIANTS}) == len(INVARIANTS)
    for invariant in INVARIANTS:
        assert invariant.tests, invariant.key
        for ref in invariant.tests:
            file, name = ref.split("::")
            assert name in function_names(folder / file), ref


def test_every_invariant_test_is_listed() -> None:
    listed = {ref for inv in INVARIANTS for ref in inv.tests}
    for path in (REPO_ROOT / INVARIANT_DIR).glob("test_*.py"):
        for name in function_names(path):
            if name.startswith("test_inv_"):
                assert f"{path.name}::{name}" in listed, f"{path.name}::{name} is not listed"


def test_the_invariant_suite_is_self_contained() -> None:
    for path in (REPO_ROOT / INVARIANT_DIR).glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from conftest" not in text and "import conftest" not in text, path.name
        assert "web_support" not in text, path.name
        assert "async def" not in text, path.name
    assert not (REPO_ROOT / INVARIANT_DIR / "conftest.py").exists()


# Export and run ----------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(
        tmp_path / "repos",
        "prat",
        files={"flag.txt": "ok", f"{INVARIANT_DIR}/test_inv_flag.py": FLAG_INVARIANT},
    )


def candidate(repo: Path, *, flag: str, invariant: str | None = None, extra: dict[str, str]) -> str:
    """Commit a candidate change on a branch; returns the trusted base commit."""
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "candidate")
    (repo / "flag.txt").write_text(flag, encoding="utf-8")
    if invariant is not None:
        (repo / INVARIANT_DIR / "test_inv_flag.py").write_text(invariant, encoding="utf-8")
    for rel, text in extra.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "candidate")
    return base


def test_a_good_candidate_passes(repo: Path, tmp_path: Path) -> None:
    base = candidate(repo, flag="ok", extra={"feature.py": "x = 1\n"})
    files = export_trusted(repo, base, tmp_path / "trusted")
    assert files == ("test_inv_flag.py",)
    result = run_invariants(tmp_path / "trusted", candidate_root=repo)
    assert result.passed, result.output_tail
    assert result.summary.startswith("trusted invariant suite passed")


def test_a_candidate_that_weakens_an_invariant_is_still_judged_by_the_original(
    repo: Path, tmp_path: Path
) -> None:
    base = candidate(
        repo,
        flag="broken",
        invariant="def test_inv_flag_is_ok():\n    assert True\n",
        extra={
            # Neither can rescue the candidate: only the trusted folder is used.
            f"{INVARIANT_DIR}/conftest.py": (
                "import pytest\n\ndef pytest_collection_modifyitems(items):\n"
                "    for item in items:\n        item.add_marker(pytest.mark.skip)\n"
            ),
            "tests/conftest.py": "raise SystemExit('candidate conftest must not load')\n",
        },
    )
    files = export_trusted(repo, base, tmp_path / "trusted")
    assert files == ("test_inv_flag.py",)
    result = run_invariants(tmp_path / "trusted", candidate_root=repo)
    assert not result.passed
    assert "test_inv_flag_is_ok" in result.output_tail


def test_a_candidate_may_add_invariants_for_later_changes(repo: Path, tmp_path: Path) -> None:
    base = candidate(
        repo,
        flag="ok",
        extra={f"{INVARIANT_DIR}/test_inv_new.py": "def test_inv_new():\n    assert False\n"},
    )
    # The candidate is judged by the old suite...
    assert export_trusted(repo, base, tmp_path / "now") == ("test_inv_flag.py",)
    assert run_invariants(tmp_path / "now", candidate_root=repo).passed
    # ...and its new invariant is part of the suite for the change after it.
    head = git(repo, "rev-parse", "HEAD")
    assert "test_inv_new.py" in export_trusted(repo, head, tmp_path / "later")


def test_a_hanging_suite_does_not_hang_the_guard(tmp_path: Path) -> None:
    folder = tmp_path / "trusted"
    folder.mkdir()
    (folder / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (folder / "test_inv_hang.py").write_text(
        "import time\n\ndef test_inv_hang():\n    time.sleep(60)\n", encoding="utf-8"
    )
    result = run_invariants(folder, timeout=3)
    assert not result.passed and result.exit_code is None
    assert result.summary == "trusted invariant suite did not finish"


def test_an_empty_suite_fails(tmp_path: Path) -> None:
    (tmp_path / "trusted").mkdir()
    result = run_invariants(tmp_path / "trusted")
    assert not result.passed
    assert "no invariant tests" in result.output_tail


def test_prats_own_suite_runs_isolated(tmp_path: Path) -> None:
    head = git(REPO_ROOT, "rev-parse", "HEAD")
    files = export_trusted(REPO_ROOT, head, tmp_path / "trusted")
    if not files:
        pytest.skip("the invariant suite is not committed yet")
    assert "invariant_support.py" in files
    result = run_invariants(
        tmp_path / "trusted", candidate_root=REPO_ROOT, log_path=tmp_path / "log"
    )
    assert result.passed, result.output_tail
    assert (tmp_path / "log").read_text(encoding="utf-8")
