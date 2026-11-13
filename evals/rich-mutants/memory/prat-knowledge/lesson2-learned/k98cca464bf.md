+++
id = "k98cca464bf"
kind = "decision"
summary = "load() is cached and widths are binary-searched in cells.py"
paths = ["rich/cells.py", "rich/_unicode_data/__init__.py", "rich/_unicode_data/unicode10-0-0.py", "rich/_unicode_data/unicode4-1-0.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:45+00:00"
last_verified = "2026-11-13T12:20:41+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
`load` is decorated with `functools.cache`, so each version module is imported once. cells.py `get_character_cell_size` bisects the `widths` list by hand. It returns 0 for C0 and C1 controls before the lookup, and returns 1 for any codepoint above the last range or in no range. The tables therefore have to stay sorted and non-overlapping. They start with `(0, 0, 0)`, except 4.1.0, which starts with control-character ranges.
