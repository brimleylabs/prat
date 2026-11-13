+++
id = "k9df797ce5a"
kind = "pitfall"
summary = "Table width calculation runs in several passes and changes in one place can break the others."
paths = ["rich/table.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:18+00:00"
last_verified = "2026-11-13T05:41:18+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
`_calculate_column_widths` measures the columns, distributes flexible `ratio` columns, shrinks them with `_collapse_widths` and `ratio_reduce` when too wide, then expands them again for `expand` or `min_width`. `__rich_measure__` and `_render` both call it, and `_get_padding_width` must match the padding logic in `_get_cells`, including `collapse_padding` and `pad_edge`. `_extra_width` accounts for the border and column dividers.
