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
