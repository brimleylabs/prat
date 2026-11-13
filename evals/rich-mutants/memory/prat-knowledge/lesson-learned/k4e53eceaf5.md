+++
id = "k4e53eceaf5"
kind = "pitfall"
summary = "Pretty and traceback tests depend on the Python version and file path"
paths = ["tests/test_pretty.py", "tests/test_traceback.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:22+00:00"
last_verified = "2026-11-13T06:49:48+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
`tests/test_pretty.py` defines `skip_py38` through `skip_py315` markers, because repr output for broken attrs objects differs between versions. `test_attrs_broken_310` branches on `sys.version_info >= (3, 13)`. `tests/test_traceback.py` skips version-specific cases (finely grained instructions before and from 3.11, notes from 3.11). Its `test_handler` regex expects the path fragment `rich/tests/test_traceback.py`. A checkout in a differently named directory therefore makes that test pass without checking any frames. Update the matching skip marker when a new Python version changes repr output.
