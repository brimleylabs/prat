+++
id = "k474bfb7ee0"
kind = "layout"
summary = "One test file per rich module, with shared helpers imported from tests/render.py"
paths = ["tests/test_syntax.py", "tests/test_progress.py", "tests/test_pick.py", "tests/test_stack.py", "tests/test_tools.py", "tests/test_ratio.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:22+00:00"
last_verified = "2026-11-13T06:49:48+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
Test files are named `test_<module>.py` and map to `rich/<module>.py` (for example `test_ratio.py` to `rich/_ratio.py`). Several files import helpers via `from .render import render` or `replace_link_ids`, so `tests` is a package and `tests/render.py` is shared. Small private-helper files (`test_pick.py`, `test_stack.py`, `test_tools.py`) hold plain unit tests with no Console.
