+++
id = "kef09cca71b"
kind = "command"
summary = "pytest config lives in tests/pytest.ini and sets junit_family=legacy"
paths = ["tests/pytest.ini", "tests/test_console.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:21+00:00"
last_verified = "2026-11-13T05:41:21+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
The only configured option is `junit_family=legacy`. Run the suite from the repo root, for example `python -m pytest tests`, or one file such as `python -m pytest tests/test_cells.py`. `test_brokenpipeerror` shells out to `python -m rich | head -1` and exits early if `which python`, `which head` or `python -m rich` fail.
