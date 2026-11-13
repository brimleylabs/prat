+++
id = "kc4a4d8bec6"
kind = "pitfall"
summary = "Circular import between cells.py and this package"
paths = ["rich/_unicode_data/__init__.py", "rich/cells.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:45+00:00"
last_verified = "2026-11-13T12:13:45+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
rich/cells.py imports `load` from `rich._unicode_data`, and each data module imports `CellTable` from rich.cells. `__init__.py` imports CellTable only under TYPE_CHECKING, and data modules are imported lazily inside `load()`. Do not add top-level imports of rich.cells to `__init__.py` or `_versions.py`.
