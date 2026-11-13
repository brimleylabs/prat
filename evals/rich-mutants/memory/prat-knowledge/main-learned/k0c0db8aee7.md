+++
id = "k0c0db8aee7"
kind = "convention"
summary = "asv benchmark naming: classes with setup() and time_* methods"
paths = ["benchmarks/benchmarks.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:12+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
Each suite class builds its fixtures in `setup()`, and every timed method is named `time_*`. Console fixtures are created the same way: `Console(file=StringIO(), color_system="truecolor", legacy_windows=False)`, often with a fixed `width`. This keeps output off the terminal and makes runs reproducible.
