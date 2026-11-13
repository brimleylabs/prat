+++
id = "kbbbf5ae1f5"
kind = "pitfall"
summary = "Table._get_padding_width collapse_padding branch looks wrong and drifts from _get_cells"
paths = ["rich/table.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:44+00:00"
last_verified = "2026-11-13T12:13:44+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
When `collapse_padding` is set, `_get_padding_width` sets `pad_left = 0` and then computes `pad_right = abs(pad_left - pad_right)`, which always equals `pad_right`. It also ignores `column_index`. `_get_cells.get_padding` instead uses `left = max(0, left - right)` for non-first columns. The two computations need to agree, because column width measurement and cell rendering both depend on them. Check this first for column-width bugs involving `Table.grid` (which uses `collapse_padding=True`) or `collapse_padding=True`.
