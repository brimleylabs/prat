+++
id = "k4ecbb0a574"
kind = "decision"
summary = "`rich.reconfigure` swaps the global Console's `__dict__` instead of rebinding the module variable."
paths = ["rich/__init__.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:19+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
`reconfigure()` builds a new `Console` and assigns `_console.__dict__ = new_console.__dict__` on the existing global instance. Code that already holds a reference from `get_console()` therefore sees the new settings. `rich.print` always renders through `get_console()` unless `file=` is given, in which case it creates a throwaway `Console(file=file)`.
