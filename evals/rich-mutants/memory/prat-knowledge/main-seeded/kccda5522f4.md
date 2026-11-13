+++
id = "kccda5522f4"
kind = "pitfall"
summary = "Table column widths are computed in several interdependent passes"
paths = ["rich/table.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:22+00:00"
last_verified = "2026-11-13T05:38:22+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
`_calculate_column_widths` measures the columns, distributes flexible `ratio` widths, collapses wrappable columns with `_collapse_widths`, and then falls back to `ratio_reduce`. It then re-measures at the reduced widths and pads when `expand` or `min_width` applies. `__rich_measure__`, `_extra_width` (border width) and `_get_padding_width` feed into these steps, so a change to padding or box handling must be made consistently in `_get_cells`, `_get_padding_width` and `_extra_width`.
