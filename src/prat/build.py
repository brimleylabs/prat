"""Which commit of PRAT is running.

``PRAT_BUILD_COMMIT`` wins when it is set (the staged trial and installed
releases set it). Otherwise, when PRAT runs from a git checkout of its own
source, that checkout's HEAD. Otherwise unknown.
"""

from __future__ import annotations

import functools
import os
import re

_COMMIT = re.compile(r"^[0-9a-f]{7,64}$")


@functools.cache
def build_commit() -> str | None:
    value = os.environ.get("PRAT_BUILD_COMMIT", "").strip().lower()
    if value:
        return value if _COMMIT.match(value) else None
    from prat.git import Git
    from prat.improve.selfproject import find_running_checkout

    checkout = find_running_checkout()
    if checkout is None:
        return None
    result = Git(checkout, timeout=10).run("rev-parse", "HEAD", check=False)
    head = result.stdout.strip().lower()
    return head if result.ok and _COMMIT.match(head) else None
