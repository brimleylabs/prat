+++
id = "k375efe32ed"
kind = "pitfall"
summary = "Golden strings are long and fragile; a small renderer change breaks many tests"
paths = ["tests/test_syntax.py", "tests/test_progress.py", "tests/test_table.py", "tests/test_panel.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:22+00:00"
last_verified = "2026-11-13T05:41:22+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
The expected strings in `tests/test_syntax.py`, `tests/test_progress.py`, `tests/test_table.py` and `tests/test_panel.py` are very long single-line escaped strings. They encode exact style splitting, colour numbers and padding. Each test prints `repr(result)`, so on a failure copy the new output and check the diff by eye before updating `expected`. Some files (`test_panel.py`, `test_progress.py`, `test_table.py`) have a `__main__` block that regenerates the output for this purpose. The `__main__` block in `test_syntax.py` references an undefined `markdown`, so it will not run as written.
