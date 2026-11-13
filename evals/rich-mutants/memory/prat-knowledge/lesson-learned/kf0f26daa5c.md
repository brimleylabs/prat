+++
id = "kf0f26daa5c"
kind = "pitfall"
summary = "Avoid circular imports in rich by importing heavy modules lazily inside functions or under TYPE_CHECKING; bar.py imports Console at module level."
paths = ["rich/__init__.py", "rich/_extension.py", "rich/_log_render.py", "rich/box.py", "rich/bar.py", "rich/cells.py", "rich/_unicode_data/__init__.py"]
status = "active"
confidence = "confirmed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:15+00:00"
last_verified = "2026-11-13T05:41:15+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"

[[sources]]
note = "deep scan, 20261113T053836"
+++
rich/__init__.py imports Console only under TYPE_CHECKING or inside function bodies, and cells.py with _unicode_data import heavy modules inside functions. rich/_extension.py imports pretty and traceback inside load_ipython_extension ('prevent circular import'), while _log_render.py and box.py guard Console behind TYPE_CHECKING. bar.py imports Console at module level, so take care when adding imports to console.py; moving lazy imports to module level can create import cycles.
