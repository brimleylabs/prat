+++
id = "k9c00db5ea6"
kind = "convention"
summary = "Regression tests cite the upstream issue number in the docstring"
paths = ["tests/test_panel.py", "tests/test_table.py", "tests/test_syntax.py", "tests/test_text.py", "tests/test_traceback.py", "tests/test_pretty.py"]
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
Bug-fix tests carry a docstring or comment such as 'Regression test for https://github.com/Textualize/rich/issues/NNNN'. Examples are the Panel title/background tests, the Table highlight and padding tests, the Syntax padding tests, the Text soft-wrap tests and the recursive traceback test. New regression tests should follow this pattern and sit beside the related existing tests.
