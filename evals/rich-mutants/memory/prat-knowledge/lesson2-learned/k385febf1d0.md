+++
id = "k385febf1d0"
kind = "pitfall"
summary = "Progress tests depend on fake clocks and exact timing values"
paths = ["tests/test_progress.py", "tests/test_status.py", "tests/test_spinner.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:46+00:00"
last_verified = "2026-11-13T12:20:41+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
Progress tests inject `get_time` (the `MockClock` class or a nonlocal counter that increments on each call) and use `auto_refresh=False`, so the elapsed and remaining times in the expected output are deterministic. Adding or removing a call to the clock inside `rich.progress` shifts those timings. `test_track_thread` and `test_status` use real `sleep`, so they are time-sensitive and asserted only loosely.
