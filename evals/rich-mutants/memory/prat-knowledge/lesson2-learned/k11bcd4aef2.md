+++
id = "k11bcd4aef2"
kind = "layout"
summary = "Windows console tests are gated and mock the Win32 API"
paths = ["tests/test_win32_console.py", "tests/test_windows_renderer.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:46+00:00"
last_verified = "2026-11-13T12:20:41+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
test_win32_console.py only defines its tests inside `if sys.platform == "win32"` and patches `_win32_console` functions (GetStdHandle, SetConsoleTextAttribute and others) on `LegacyWindowsTerm`. test_windows_renderer.py wraps its imports in try/except, sets `pytestmark` skipif for non-win32, and uses `create_autospec(LegacyWindowsTerm)`. Both are skipped entirely on other platforms, so a break there only shows up on Windows.
