+++
id = "keb8a788ca1"
kind = "pitfall"
summary = "Changing rendering output breaks big hard-coded expected blobs"
paths = ["tests/test_syntax.py", "tests/test_progress.py", "tests/test_table.py", "tests/test_panel.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:46+00:00"
last_verified = "2026-11-13T12:13:46+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
Many tests hold single-line expected strings, with full ANSI-styled screens in test_syntax and test_progress and a multi-width table dump in test_table. A small change to Style, Segment, Panel, Table or Syntax rendering can break many of them at once. Each one needs a careful regeneration, not a blind paste. The `__main__` blocks in test_panel.py, test_progress.py and test_table.py print fresh output for that purpose.
