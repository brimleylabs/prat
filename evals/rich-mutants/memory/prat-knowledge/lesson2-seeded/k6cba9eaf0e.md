+++
id = "k6cba9eaf0e"
kind = "pitfall"
summary = "Segment line helpers have subtle width and control-code rules"
paths = ["rich/segment.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:43+00:00"
last_verified = "2026-11-13T12:13:43+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
Segments with `control` set count as zero cells (`cell_length`, `get_line_length`, `divide`) and are skipped by `apply_style`, `strip_styles` and `simplify` merging. `adjust_line_length` crops via `set_cell_size`. Splitting a double-width character at a cut turns it into two spaces, so output width is preserved. `split_lines` yields a trailing line only if it is non-empty. `split_and_crop_lines` reuses and clears the same `line` list object, so callers must consume each yielded list immediately.
