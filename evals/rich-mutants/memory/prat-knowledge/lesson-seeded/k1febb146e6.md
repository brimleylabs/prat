+++
id = "k1febb146e6"
kind = "convention"
summary = "Regression tests are added per bug and cite the upstream issue number in the docstring"
paths = ["tests/test_panel.py", "tests/test_text.py", "tests/render.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:25+00:00"
last_verified = "2026-11-13T05:41:25+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
Fixes come with a test in the matching tests/test_<module>.py, and the docstring references the issue. Shared helpers are imported from tests/render.py.
