+++
id = "kac7d644be8"
kind = "layout"
summary = "One test file per rich module, plus a few helper-level files"
paths = ["tests/test_unicode_data.py", "tests/test_tools.py", "tests/test_ratio.py", "tests/test_panel.py", "tests/test_table.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:25+00:00"
last_verified = "2026-11-13T05:38:25+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
Files map roughly one-to-one to `rich/<name>.py`, for example test_segment.py to rich/segment.py and test_table.py to rich/table.py. Private helpers get their own files: test_pick.py, test_ratio.py, test_stack.py, test_tools.py (which tests `rich/_loop.py` and `ratio_distribute`), and test_unicode_data.py. `test_unicode_data.py` asserts that `load()` picks the nearest lower Unicode version, falls back to 4.1.0 below the range, and falls back to 17.0.0 for invalid strings. It hard-codes those version numbers, so update them when `rich/_unicode_data` gains a version. Some files, such as test_panel.py, test_table.py, test_progress.py and test_syntax.py, have a `__main__` block that regenerates expected output.
