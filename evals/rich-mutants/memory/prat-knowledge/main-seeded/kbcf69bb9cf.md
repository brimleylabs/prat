+++
id = "kbcf69bb9cf"
kind = "pitfall"
summary = "Progress, Status and Live tests use fake clocks and a refresh thread"
paths = ["rich/progress.py", "rich/live.py", "tests/test_progress.py", "tests/test_status.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:29+00:00"
last_verified = "2026-11-13T05:38:29+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
Progress runs a background Live refresh thread with a shared RLock. Its tests inject fake time sources, so refresh timing and elapsed values are part of the expected output. Changing refresh logic changes those expectations.
