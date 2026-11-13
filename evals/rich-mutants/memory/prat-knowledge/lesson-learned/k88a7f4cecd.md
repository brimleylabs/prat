+++
id = "k88a7f4cecd"
kind = "layout"
summary = "rich/_unicode_data: rich/_unicode_data holds Unicode cell-width tables for rich's terminal width measurement, one generated module per Unicode version from 4.1.0 to 17.0.0"
paths = ["rich/_unicode_data"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:19+00:00"
last_verified = "2026-11-13T06:49:48+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
rich/_unicode_data holds Unicode cell-width tables for rich's terminal width measurement, one generated module per Unicode version from 4.1.0 to 17.0.0. `__init__.py` exposes `load(unicode_version)`, which picks a version and imports the matching `unicodeX-Y-Z.py` module lazily. Each data module builds a `rich.cells.CellTable(unicode_version, widths, narrow_to_wide)` named `cell_table`. `widths` is a sorted list of `(start, end, width)` codepoint ranges and `narrow_to_wide` is a frozenset of characters. `rich/cells.py` is the consumer: `get_character_cell_size`, `_cell_len` and `split_graphemes` call `load_cell_table`. `tests/test_unicode_data.py` checks version parsing and selection. I read `__init__.py`, `_versions.py`, `unicode17-0-0.py` (the first 120 lines and everything from line 440 on) and the first 250 matching lines of `unicode4-1-0.py`, which covered its whole table. I did not open the other data files, so what I say about them is inferred from those samples and from the headers and layout the Grep results showed.
