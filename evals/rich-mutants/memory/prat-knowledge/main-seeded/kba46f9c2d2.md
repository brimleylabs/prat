+++
id = "kba46f9c2d2"
kind = "command"
summary = "Run a module's demo with python -m rich.<module> to check rendering by eye"
paths = ["rich/spinner.py", "rich/syntax.py", "rich/progress.py", "rich/prompt.py", "rich/status.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:22+00:00"
last_verified = "2026-11-13T05:38:22+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
Most of these files have a `__main__` block that works as a manual demo: `python -m rich.progress`, `rich.spinner` (lists all spinners), `rich.status`, `rich.prompt`, `rich.panel`, `rich.rule`, `rich.pretty`, `rich.scope`, `rich.segment`, `rich.palette`, `rich.progress_bar` and `rich.repr`. `python -m rich.syntax PATH` is a real CLI built on argparse, with flags such as `-l`, `-i`, `-r`, `-t` and `-x`. These blocks are excluded from coverage and run indefinitely or interactively in some cases (for example `rich.spinner`).
