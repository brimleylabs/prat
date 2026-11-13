+++
id = "kf4ee8d4d09"
kind = "convention"
summary = "Width table data conventions (sorted ranges, widths 0 or 2, default 1)"
paths = ["rich/cells.py", "rich/_unicode_data/unicode4-1-0.py", "rich/_unicode_data/unicode17-0-0.py"]
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
Ranges in `widths` are sorted by start codepoint and non-overlapping, because rich/cells.py binary-searches them. Only width 0 (combining or zero-width) and width 2 (wide) appear, and any codepoint not found, or above the last range's end, counts as width 1. Controls below 32 and 0x7F-0x9F are handled in `get_character_cell_size` before the table is consulted. The oldest tables (4.1.0 to 5.0.0) also list control and noncharacter ranges as width 0, such as `(0, 8, 0)` and `(1114110, 1114111, 0)`. Newer ones start at `(0, 0, 0)`.
