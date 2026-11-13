+++
id = "kdebf070ba1"
kind = "convention"
summary = "Snapshot tests render through a Console writing to StringIO and compare against literal escape-sequence strings"
paths = ["tests/test_console.py", "tests/test_live.py", "tests/test_align.py", "tests/test_logging.py", "tests/test_layout.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:24+00:00"
last_verified = "2026-11-13T05:38:24+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
Typical tests use Console(file=io.StringIO(), width=N, color_system="truecolor" or None, force_terminal=True, legacy_windows=False, _environ={}) and assert console.file.getvalue() == "...\x1b[..m...". Passing `_environ={}` and `legacy_windows=False` makes output independent of the host environment, so new tests should set them. Many tests also print(repr(result)) so the expected string can be pasted back in.
