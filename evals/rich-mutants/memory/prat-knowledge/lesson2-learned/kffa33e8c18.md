+++
id = "kffa33e8c18"
kind = "layout"
summary = "Test files map roughly one-to-one onto rich modules, and Console behaviour lives in test_console.py."
paths = ["tests/test_console.py", "tests/test_jupyter.py", "tests/test_cells.py", "tests/test_highlighter.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:45+00:00"
last_verified = "2026-11-13T12:13:45+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
Most files are named `test_<module>.py` for the matching `rich/<module>.py`. test_console.py (about 1100 lines) covers env-var detection (TERM, COLORTERM, FORCE_COLOR, TTY_COMPATIBLE, TTY_INTERACTIVE, COLUMNS, LINES), print and log, export to text/HTML/SVG, pager, capture and screen. The `jupyter` tests cover JUPYTER_COLUMNS and JUPYTER_LINES via `force_jupyter=True`. The cell-width and grapheme tests in test_cells.py (`split_graphemes`, `split_text`, `chop_cells`) pin Unicode behaviour that comes from `rich/cells.py` and `rich/_unicode_data`.
