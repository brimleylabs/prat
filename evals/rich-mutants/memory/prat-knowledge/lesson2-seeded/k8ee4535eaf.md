+++
id = "k8ee4535eaf"
kind = "convention"
summary = "Tests are golden-output: exact strings, including ANSI codes, from an in-memory Console"
paths = ["tests/render.py", "tests/conftest.py", "tests/test_console.py", "tests/test_table.py", "tests/test_syntax.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:47+00:00"
last_verified = "2026-11-13T12:13:47+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
Tests build Console(file=io.StringIO(), ...) or use capture()/record=True with fixed width and color system, then compare the output to literal expected strings. The shared helper is tests/render.py. tests/conftest.py removes FORCE_COLOR and NO_COLOR for every test. Any change to rendering output therefore shows up as expected-string diffs across many test files.
