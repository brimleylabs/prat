+++
id = "k1c2dd8fa31"
kind = "pitfall"
summary = "Segment is a NamedTuple whose cell-width logic is performance-sensitive and must respect control segments"
paths = ["rich/segment.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:17+00:00"
last_verified = "2026-11-13T06:49:48+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
`Segment` is `(text, style, control)`. Control segments have `cell_length` 0 and are skipped by `apply_style`, `adjust_line_length` and `divide`. `split_cells` has a fast path for single-cell text and caches the slow path with lru_cache. Cutting through a double-width character replaces it with two spaces. Preserve these invariants when editing the helpers (`split_lines`, `divide`, `set_shape`, `align_*`, `simplify`).
