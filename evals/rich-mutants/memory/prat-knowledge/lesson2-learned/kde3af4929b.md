+++
id = "kde3af4929b"
kind = "pitfall"
summary = "Some test expectations depend on Python version, platform or timing"
paths = ["tests/test_inspect.py", "tests/test_pretty.py", "tests/test_traceback.py", "tests/test_progress.py", "tests/test_logging.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:48+00:00"
last_verified = "2026-11-13T12:13:48+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
test_inspect, test_pretty, test_traceback and test_syntax have version- or platform-dependent output behind skip markers. Progress, status and spinner tests use fake clocks. Logging and Live tests touch global state. A pass on one interpreter does not prove a pass on all of 3.10–3.15.
