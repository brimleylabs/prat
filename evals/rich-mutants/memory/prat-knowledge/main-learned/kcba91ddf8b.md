+++
id = "kcba91ddf8b"
kind = "layout"
summary = "rich/_unicode_data: rich/_unicode_data holds the per-Unicode-version width tables that rich/cells.py uses to measure how many terminal cells a character takes"
paths = ["rich/_unicode_data"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:23+00:00"
last_verified = "2026-11-13T05:38:23+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
rich/_unicode_data holds the per-Unicode-version width tables that rich/cells.py uses to measure how many terminal cells a character takes. There are 21 generated files, one per version from 4.1.0 to 17.0.0, named like `unicode16-0-0.py`. Each one builds a `CellTable(unicode_version, widths, narrow_to_wide)`. `widths` is a sorted list of `(start, end, width)` codepoint ranges with width 0 or 2, and `narrow_to_wide` is a frozenset of characters (digits, `#`, `*`, `©`, `™`, and many symbols and emoji). `__init__.py` exposes `load()`, which picks a version and imports the matching module lazily, and `_versions.py` lists the available versions in `VERSIONS`. rich/cells.py imports `load` as `load_cell_table`. tests/test_unicode_data.py covers loading, version parsing and version selection.
