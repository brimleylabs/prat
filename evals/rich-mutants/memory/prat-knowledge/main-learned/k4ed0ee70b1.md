+++
id = "k4ed0ee70b1"
kind = "pitfall"
summary = "Examples track the public rich API, so API renames can break them silently"
paths = ["examples/downloader.py", "examples/fullscreen.py", "examples/dynamic_progress.py", "examples/overflow.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:18+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
Nothing in examples/ is imported by the library, and the examples are not shown to be run in tests. Renames or signature changes in rich/progress.py, rich/console.py, rich/layout.py or rich/live.py would only show up when a demo is run by hand. After changing those APIs, grep examples/ for the affected names. examples/downloader.py, for instance, uses `Progress.console.log`, `start_task` and `TaskID`.
