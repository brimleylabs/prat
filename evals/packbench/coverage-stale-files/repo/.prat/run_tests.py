"""Run the fast part of coverage.py's test suite with the plugins this machine has.

    python .prat/run_tests.py [pytest arguments]

Without arguments it runs the data, SQLite, command line and API tests. Arguments are
passed to pytest and replace that default list of test files.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TESTS = [
    "tests/test_data.py",
    "tests/test_sqlitedb.py",
    "tests/test_cmdline.py",
    "tests/test_api.py",
]

OPTIONS = [
    "-q",
    "-p", "no:cacheprovider",
    # The project's own options ask for pytest-xdist and flaky, which are not installed.
    "-o", "addopts=",
    "-o", "strict=false",
    "-o", "verbosity_assertions=1",
    # Needs the right to create symbolic links, which Windows withholds by default.
    "--deselect", "tests/test_api.py::ApiTest::test_stdlib_symlink",
]


def main() -> int:
    # The C tracer is not built here, so the tests measure with the Python tracer.
    os.environ["COVERAGE_CORE"] = "pytrace"
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    import pytest

    return int(pytest.main(OPTIONS + (sys.argv[1:] or DEFAULT_TESTS)))


if __name__ == "__main__":
    sys.exit(main())
