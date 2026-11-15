"""Runs one job's command and writes how it ended (``python -m prat.jobrunner STATUS -- CMD``).

PRAT starts jobs through this wrapper so that their exit code is known even when PRAT itself
restarted while they ran: the wrapper waits for the command and writes ``{"exit_code": n,
"finished_at": t}`` to the status file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "--":
        print("usage: python -m prat.jobrunner STATUS_FILE -- COMMAND...", file=sys.stderr)
        return 2
    status = Path(argv[0])
    command = argv[2:]
    started = time.time()
    try:
        code = subprocess.call(command)
    except OSError as exc:
        print(f"could not start the job: {exc}", file=sys.stderr)
        code = 127
    except KeyboardInterrupt:
        code = 130
    tmp = status.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps({"exit_code": code, "started_at": started, "finished_at": time.time()}),
        encoding="utf-8",
    )
    os.replace(tmp, status)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
