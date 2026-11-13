+++
id = "k9efad9de0c"
kind = "pitfall"
summary = "Windows-only tests are skipped on other platforms and import guarded"
paths = ["tests/test_win32_console.py", "tests/test_windows_renderer.py", "tests/test_tree.py"]
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
`tests/test_win32_console.py` defines all its tests inside an `if sys.platform == "win32"` block and mocks the ctypes calls in `rich._win32_console`. `tests/test_windows_renderer.py` wraps its imports in try/except and sets a module-level `pytestmark` skipif. Tests for `rich/_win32_console.py` and `rich/_windows_renderer.py` therefore only run on Windows. Tree tests also have separate win32 and non-win32 variants that expect different box characters.
