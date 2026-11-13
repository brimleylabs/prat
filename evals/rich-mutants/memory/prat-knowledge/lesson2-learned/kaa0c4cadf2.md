+++
id = "kaa0c4cadf2"
kind = "layout"
summary = "Table width calculation is a multi-step pipeline"
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
`_calculate_column_widths` measures each column with `_measure_column`, distributes flex ratios when `expand` is set (`ratio_distribute`), shrinks the widest wrappable columns with `_collapse_widths`, then falls back to `ratio_reduce` and re-measures. It finally pads out to `expand`/`min_width`. `_extra_width` counts border cells, and `__rich_console__`, `__rich_measure__` and `_render` all rely on it. `_render` builds rows with `_get_cells`, renders each cell with `console.render_lines`, then aligns vertically with `Segment.align_*` and `set_shape`.
