+++
id = "k4ca45f9fe2"
kind = "layout"
summary = "Table file layout and the CellTable contract"
paths = ["rich/_unicode_data/__init__.py", "rich/cells.py", "rich/_unicode_data/unicode16-0-0.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 1
hurt = 0
created = "2026-11-13T05:38:23+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
Each file does `from rich.cells import CellTable` and defines a module-level `cell_table = CellTable("X.Y.Z", [ranges...], frozenset([...]))`. `CellTable` is a NamedTuple defined in rich/cells.py with fields `unicode_version`, `widths` and `narrow_to_wide`. `load()` returns `module.cell_table`, so every table module must keep that exact name. Because the table modules import from rich.cells and cells.py imports rich._unicode_data, `__init__.py` imports `CellTable` only under `TYPE_CHECKING` to avoid a circular import.
