+++
id = "k4ce172bce9"
kind = "pitfall"
summary = "Test-suite leftovers: shadowed duplicate test and unused fixture in test_console.py"
paths = ["tests/test_console.py"]
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
test_console.py has a test name defined twice, so the first definition never runs, and an unused SVG fixture. Keep this in mind when editing or adding tests there.
