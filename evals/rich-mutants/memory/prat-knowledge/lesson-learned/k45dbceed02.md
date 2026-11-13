+++
id = "k45dbceed02"
kind = "pitfall"
summary = "make_width_tables.py depends on the CellTable constructor in rich/cells.py"
paths = ["tools/make_width_tables.py", "rich/cells.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:23+00:00"
last_verified = "2026-11-13T06:49:48+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
The script builds `CellTable(version, table, frozenset(...))` and reads `.unicode_version`, `.widths` and `.narrow_to_wide`. Changing that class's signature or attributes breaks the generator and the generated files, which are imported as `from rich.cells import CellTable`.
