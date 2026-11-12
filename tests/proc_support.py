"""Helpers for tests that check a whole process tree was killed.

The command built here starts a child that outlives it: a "heartbeat" process
that keeps appending to a file. The parent then waits. Whether the heartbeat
was killed is read from the file: it stops growing. That needs no process ids
and no platform-specific process listing.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Appends one byte every 50 ms. Bounded, so a failing test cleans up after itself.
HEARTBEAT = """
import sys, time
end = time.monotonic() + 40
while time.monotonic() < end:
    with open(sys.argv[1], "a") as handle:
        handle.write("x")
    time.sleep(0.05)
"""

# Starts the heartbeat on the same output streams (as a test runner's workers
# would be), then waits far longer than any test allows.
PARENT = """
import subprocess, sys, time
subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[2]], stdout=1, stderr=2)
print("parent started", flush=True)
time.sleep(40)
"""


def tree_argv(heartbeat: Path) -> list[str]:
    """A command that starts a heartbeat process and then hangs."""
    return [sys.executable, "-c", PARENT, HEARTBEAT, str(heartbeat)]


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def wait_for_heartbeat(path: Path, timeout: float = 30.0) -> bool:
    """Wait until the heartbeat process has written at least once."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _size(path) > 0:
            return True
        time.sleep(0.02)
    return False


def goes_quiet(path: Path, *, quiet: float = 1.0, timeout: float = 10.0) -> bool:
    """Whether the heartbeat file stops growing (for ``quiet`` seconds) within ``timeout``."""
    deadline = time.monotonic() + timeout
    last = _size(path)
    changed = time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(0.05)
        size = _size(path)
        now = time.monotonic()
        if size != last:
            last, changed = size, now
        elif now - changed >= quiet:
            return True
    return False
