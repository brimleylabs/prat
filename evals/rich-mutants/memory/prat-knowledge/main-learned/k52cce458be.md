+++
id = "k52cce458be"
kind = "pitfall"
summary = "Cell-width logic (cells.py) has fast paths and caches that must stay consistent"
paths = ["rich/cells.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 1
hurt = 0
created = "2026-11-13T05:38:21+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
`cell_len`, `set_cell_size`, `split_text` and `chop_cells` first check `_is_single_cell_widths`, a frozenset of single-cell characters, and then fall back to the unicode tables from `rich._unicode_data.load`. Characters joined by `\u200d` or followed by `\ufe0f` need special handling in both `_cell_len` and `split_graphemes`. A split that lands inside a double-width character is replaced with two spaces. `cell_len` caches results via `lru_cache` for strings under 512 characters, and `get_character_cell_size` is cached too. Any fix must keep `_cell_len` and `split_graphemes` agreeing.
