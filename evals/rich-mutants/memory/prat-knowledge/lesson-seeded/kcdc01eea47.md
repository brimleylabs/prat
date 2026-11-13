+++
id = "kcdc01eea47"
kind = "convention"
summary = "Regression tests cite upstream issue numbers in the docstring"
paths = ["tests/test_panel.py", "tests/test_syntax.py", "tests/test_table.py", "tests/test_text.py", "tests/test_style.py", "tests/test_traceback.py"]
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
Bug-fix tests carry a docstring such as 'Regression test for https://github.com/Textualize/rich/issues/NNNN', for example in `test_panel.py`, `test_syntax.py`, `test_table.py`, `test_text.py`, `test_style.py` and `test_traceback.py`. New regression tests should follow this pattern and sit next to the related tests in the same file.
