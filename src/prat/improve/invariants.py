"""The invariant suite and how the trusted copy of it judges a candidate.

``tests/invariants/`` in PRAT's repository holds what must stay true whatever
else changes. When PRAT judges a change to itself, the suite is taken from the
default branch *before* the change, written to a folder outside the
candidate's tree, and run against the candidate's installed code. A candidate
can add invariants (they apply to the changes after it); editing or deleting
one gains it nothing, because the copy that runs is the trusted one.

The run is isolated from the candidate's own test setup: a fixed minimal
pytest configuration, ``python -I`` (no ``PYTHONPATH``, no user site, no
current directory on the path) and no plugin autoloading, so neither the
candidate's ``conftest.py`` nor an entry point it registers can take part.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from prat.git import Git
from prat.proc import run_tree

INVARIANT_DIR = "tests/invariants"
DEFAULT_TIMEOUT_SECONDS = 900.0
OUTPUT_TAIL_CHARS = 6000

TRUSTED_INI = """\
[pytest]
# Written by PRAT for a trusted invariant run; the candidate's settings are not used.
addopts = -q -p no:cacheprovider
"""


@dataclass(frozen=True)
class Invariant:
    key: str
    description: str
    # "test file::test function" in tests/invariants.
    tests: tuple[str, ...]


INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        "no_agent_push",
        "An agent session cannot push, by any variant of the command.",
        (
            "test_inv_agents.py::test_inv_policy_denies_every_push_at_every_level",
            "test_inv_agents.py::test_inv_an_agent_worktree_cannot_push_for_real",
        ),
    ),
    Invariant(
        "writes_contained",
        "An agent cannot write outside its worktree or into protected or secret paths.",
        (
            "test_inv_agents.py::test_inv_writes_stay_inside_the_worktree_and_out_of_protected_paths",
        ),
    ),
    Invariant(
        "read_only_roles",
        "Read-only roles have no write tools.",
        ("test_inv_agents.py::test_inv_read_only_roles_have_no_write_tools",),
    ),
    Invariant(
        "web_tools_researcher_only",
        "Web tools are available to the researcher role only.",
        ("test_inv_agents.py::test_inv_web_tools_belong_to_the_researcher_alone",),
    ),
    Invariant(
        "web_needs_session",
        "Every page and API route refuses a request with no session; the public health "
        "check tells only liveness, version and commit.",
        (
            "test_inv_web.py::test_inv_every_route_needs_a_session",
            "test_inv_web.py::test_inv_the_health_check_tells_nothing_but_liveness",
        ),
    ),
    Invariant(
        "web_csrf_origin",
        "Every state-changing request refuses a missing or wrong CSRF token and a foreign Origin.",
        ("test_inv_web.py::test_inv_state_changing_requests_need_csrf_and_same_origin",),
    ),
    Invariant(
        "approval_bound_to_commit",
        "Approval is bound to the commit the gates and the reviewer looked at.",
        (
            "test_inv_landing.py::test_inv_approval_is_bound_to_the_reviewed_commit",
            "test_inv_landing.py::test_inv_automatic_approval_needs_a_met_verdict",
        ),
    ),
    Invariant(
        "verification_required",
        "Nothing reaches review without passed gates and a real change.",
        (
            "test_inv_landing.py::test_inv_a_failing_gate_never_reaches_review",
            "test_inv_landing.py::test_inv_an_empty_change_never_reaches_review",
        ),
    ),
    Invariant(
        "engine_push",
        "The engine push is fast-forward only, pushes only the default branch, and refuses "
        "any project that is not the self project.",
        (
            "test_inv_push.py::test_inv_the_push_is_fast_forward_only_and_for_the_self_project_alone",
        ),
    ),
    Invariant(
        "full_proof",
        "Nothing lands without passed gates, both reviewer verdicts and a passed trial, all "
        "for the same commit.",
        ("test_inv_push.py::test_inv_nothing_lands_without_full_proof",),
    ),
    Invariant(
        "security_review_required",
        "A required security review must pass; a concern or no review sends the card back.",
        ("test_inv_landing.py::test_inv_a_required_security_review_must_pass",),
    ),
    Invariant(
        "secrets_redacted",
        "Secrets are redacted from journals, the error log and the web UI.",
        (
            "test_inv_secrets.py::test_inv_journals_never_store_secrets",
            "test_inv_secrets.py::test_inv_the_error_log_never_stores_secrets",
            "test_inv_secrets.py::test_inv_the_web_ui_never_shows_agent_written_secrets",
        ),
    ),
    Invariant(
        "research_schema",
        "Research output outside the schema is rejected; weakening proposals are screened.",
        (
            "test_inv_research.py::test_inv_output_outside_the_schema_is_rejected",
            "test_inv_research.py::test_inv_proposals_that_weaken_protections_are_screened",
        ),
    ),
)


@dataclass(frozen=True)
class InvariantResult:
    passed: bool
    exit_code: int | None
    duration_seconds: float
    output_tail: str
    files: tuple[str, ...]

    @property
    def summary(self) -> str:
        if self.passed:
            return f"trusted invariant suite passed ({len(self.files)} files)"
        if self.exit_code is None:
            return "trusted invariant suite did not finish"
        return f"trusted invariant suite failed (exit {self.exit_code})"


def export_trusted(clone: Path, ref: str, dest: Path) -> tuple[str, ...]:
    """Write ``tests/invariants`` as committed at ``ref`` into ``dest``.

    Returns the relative file names written. Only regular files under the
    folder are written, and every name is checked to stay inside ``dest``.
    """
    git = Git(clone, timeout=120)
    listing = git.run("ls-tree", "-r", "-z", "--full-tree", ref, "--", INVARIANT_DIR).stdout
    written: list[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    for entry in listing.split("\0"):
        if not entry.strip():
            continue
        meta, path = entry.split("\t", 1)
        mode, kind, _sha = meta.split()
        if kind != "blob" or mode not in ("100644", "100755"):
            continue  # links and submodules are never written
        rel = PurePosixPath(path).relative_to(INVARIANT_DIR)
        if any(part in ("", ".", "..") for part in rel.parts):
            continue
        target = (dest / Path(*rel.parts)).resolve()
        if root not in target.parents:
            continue
        content = git.run("show", f"{ref}:{path}").stdout
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
        written.append(rel.as_posix())
    (dest / "pytest.ini").write_text(TRUSTED_INI, encoding="utf-8", newline="\n")
    return tuple(sorted(written))


def run_invariants(
    trusted_dir: Path,
    *,
    python: Path | None = None,
    candidate_root: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    log_path: Path | None = None,
    launcher: Callable[..., list[str]] | None = None,
) -> InvariantResult:
    """Run the trusted copy in ``trusted_dir`` with ``python`` (the candidate's
    environment). Never raises for a failing or hanging run.

    ``launcher(module, *args)`` builds the command that runs a module in the
    candidate's environment (the staged trial passes its own); the default runs
    ``python -I -X utf8 -m``.
    """
    files = tuple(
        sorted(p.relative_to(trusted_dir).as_posix() for p in trusted_dir.rglob("test_*.py"))
    )
    if not files:
        return InvariantResult(False, None, 0.0, "no invariant tests were found", files)
    child_env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PYTHON"))}
    child_env.update(env or {})
    child_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    if candidate_root is not None:
        child_env["PRAT_CANDIDATE_ROOT"] = str(candidate_root)
    args = ["-c", str(trusted_dir / "pytest.ini"), "--rootdir", str(trusted_dir), str(trusted_dir)]
    if launcher is not None:
        argv = launcher("pytest", *args)
    else:
        # -I ignores PYTHONUTF8; -X utf8 keeps the output UTF-8 on every system.
        argv = [str(python or sys.executable), "-I", "-X", "utf8", "-m", "pytest", *args]
    started = time.monotonic()
    try:
        proc = run_tree(argv, cwd=trusted_dir, env=child_env, timeout=timeout)
    except Exception as exc:
        return InvariantResult(
            False, None, time.monotonic() - started, f"{type(exc).__name__}: {exc}", files
        )
    output = (proc.stdout + proc.stderr)[-OUTPUT_TAIL_CHARS:]
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(proc.stdout + proc.stderr, encoding="utf-8")
    return InvariantResult(
        proc.returncode == 0, proc.returncode, time.monotonic() - started, output, files
    )
