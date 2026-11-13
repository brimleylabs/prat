+++
id = "k4a032edce4"
kind = "pitfall"
summary = "Some tests leak global state"
paths = ["tests/test_logging.py", "tests/test_traceback.py", "tests/test_rich_print.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:29+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
test_logging.py adds handlers to the root logger and mostly never removes them. Other tests depend on the global Console or on the repo layout, for example traceback paths. Keep this in mind when a test passes alone but fails in the full suite, or the other way round.
