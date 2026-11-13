+++
id = "k1e5d450ace"
kind = "convention"
summary = "Renderables use the `__rich_console__` and `__rich_measure__` protocol and extend `JupyterMixin`."
paths = ["rich/align.py", "rich/bar.py", "rich/_inspect.py", "rich/abc.py"]
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
`Align`, `Bar`, `VerticalCenter` and `Inspect` derive from `JupyterMixin`. They implement `__rich_console__(console, options)` yielding `Segment`s, plus `__rich_measure__` returning a `Measurement` where width matters. `Inspect` uses `__rich__` instead. Imports from `.console` are under `TYPE_CHECKING` in `align.py` and `_log_render.py`, but `bar.py` imports `Console` directly at runtime.
