+++
id = "k1deaffb878"
kind = "decision"
summary = "Unicode width behaviour is pinned by explicit grapheme and cell-width tests"
paths = ["tests/test_cells.py", "rich/cells.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:21+00:00"
last_verified = "2026-11-13T05:41:21+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
`test_cells.py` fixes how zero-width joiners, variation selector 16, lone escapes and private-use (nerd font) code points are measured. It also fixes how `split_text` and `chop_cells` split inside double-width characters, using spaces as padding. Changes to `rich/cells.py` must keep these cases passing. The `test_zwj` comment says the result deliberately matches wcwidth.
