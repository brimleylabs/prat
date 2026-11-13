+++
id = "k2a3da7abd3"
kind = "convention"
summary = "Data module layout and file naming"
paths = ["rich/_unicode_data/__init__.py", "rich/_unicode_data/unicode15-1-0.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:19+00:00"
last_verified = "2026-11-13T05:41:19+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
Each module has only the header comment, `from rich.cells import CellTable`, and `cell_table = CellTable("X.Y.Z", [ (start, end, width), ... ], frozenset([...chars...]))`. The file name is `unicode` plus the version with dots changed to dashes (`unicode15-1-0.py`). `load()` builds the module name with `version.replace(".", "-")`, so the name must match exactly.
