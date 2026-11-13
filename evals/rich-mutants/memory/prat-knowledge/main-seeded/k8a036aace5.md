+++
id = "k8a036aace5"
kind = "command"
summary = "Run a module directly with `python -m rich.<module>` to see its demo. `python -m rich` renders the full feature test card."
paths = ["rich/__main__.py", "rich/box.py", "rich/align.py", "rich/_windows.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:20+00:00"
last_verified = "2026-11-13T05:38:20+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
`rich/__main__.py` builds `make_test_card()` and times a cold and a warm render. `box.py`, `align.py`, `_log_render.py`, `_wrap.py`, `abc.py`, `_win32_console.py` (Windows only), `_windows.py` and `ansi.py` (POSIX only, takes a command to spawn in a pty) all have `__main__` blocks marked `# pragma: no cover`. These are quick visual checks when changing rendering code.
