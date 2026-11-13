+++
id = "kc0de961dc5"
kind = "convention"
summary = "Import-cycle avoidance: lazy imports and TYPE_CHECKING"
paths = ["rich/__init__.py", "rich/_log_render.py", "rich/_extension.py", "rich/align.py", "rich/box.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:42+00:00"
last_verified = "2026-11-13T12:13:42+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
`rich/__init__.py` imports `Console` only inside functions and keeps the global `_console` as `None` until `get_console()` runs. `rich/_log_render.py`, `rich/align.py` and `rich/box.py` import `Console` or `ConsoleOptions` only under `TYPE_CHECKING`, or inside the function that needs them. `rich/_extension.py` imports `rich.pretty` and `rich.traceback` inside the function to avoid a circular import. Keep new imports of `rich.console` or `rich.table` inside functions in these modules.
