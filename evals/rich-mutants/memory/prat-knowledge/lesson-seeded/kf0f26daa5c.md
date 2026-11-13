+++
id = "kf0f26daa5c"
kind = "pitfall"
summary = "Circular-import avoidance: heavy modules are imported lazily inside functions, and some modules import `Console` unguarded."
paths = ["rich/__init__.py", "rich/_extension.py", "rich/_log_render.py", "rich/box.py", "rich/bar.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:15+00:00"
last_verified = "2026-11-13T05:41:15+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
`rich/__init__.py` imports `Console` only under `TYPE_CHECKING` or inside function bodies. `rich/_extension.py` imports `pretty` and `traceback` inside `load_ipython_extension` with the comment 'prevent circular import'. `_log_render.py` and `box.py` guard `Console` behind `TYPE_CHECKING`. `bar.py` imports `Console` at module level, so keep that in mind when adding imports to `console.py`.
