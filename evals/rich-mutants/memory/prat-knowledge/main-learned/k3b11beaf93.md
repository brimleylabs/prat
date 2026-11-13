+++
id = "k3b11beaf93"
kind = "convention"
summary = "Scripts are flat top-level scripts with no main guard, so importing one runs it"
paths = ["tools/make_width_tables.py", "tools/profile_divide.py", "tools/stress_test_pretty.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:26+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
Every Python file in tools/ runs its work at import time and has no functions, tests or argparse. Keep any new tool in the same style. Any new script should also avoid being picked up by test collection.
