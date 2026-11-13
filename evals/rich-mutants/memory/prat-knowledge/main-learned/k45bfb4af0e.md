+++
id = "k45bfb4af0e"
kind = "pitfall"
summary = "tests/_card_render.py is a generated, never-hand-edited snapshot of make_test_card() output that no test currently asserts against; regenerate it via test_card.py's __main__ block."
paths = ["tests/_card_render.py", "tests/test_card.py", "rich/__main__.py"]
status = "active"
confidence = "confirmed"
author = "checker"
helped = 0
hurt = 0
created = "2026-11-13T05:47:44+00:00"
last_verified = "2026-11-13T05:47:44+00:00"
invalid_at = ""
superseded_by = ""

[[links]]
to = "k75059d509e"
relation = "refines"

[[sources]]
note = "deep scan, 20261113T053544"

[[sources]]
note = "checked against aa3a3c47b423"

[[sources]]
note = "deep scan, 20261113T053544"
+++
tests/_card_render.py is a single line, `expected = ...`, holding the full escape-coded output of rich.__main__.make_test_card() at width 100. It is written by the `__main__` block of tests/test_card.py using open("_card_render.py", "wt"), so it lands in the current directory; run it so the output ends up in tests/ (running from inside tests/ may fail on the relative import, in which case use `python -m tests.test_card` from the repo root and move the file into tests/). No test currently compares against the file, so visual changes to the card in rich/__main__.py do not fail tests; never edit it by hand, and regenerate it whenever the test card changes.
