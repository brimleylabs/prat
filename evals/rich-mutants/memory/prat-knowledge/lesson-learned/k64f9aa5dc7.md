+++
id = "k64f9aa5dc7"
kind = "decision"
summary = "Traceback uses its own inheriting-nothing theme built from a Pygments style and does not use the console's theme."
paths = ["rich/traceback.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:19+00:00"
last_verified = "2026-11-13T05:41:19+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
`Traceback.__rich_console__` builds a `Theme(..., inherit=False)` that maps Rich style names such as `repr.*`, `scope.*` and `pygments.*` to tokens from the Pygments theme (default `ansi_dark`). It applies the theme with `console.use_theme` only around the panels. Frames that set `_rich_traceback_omit` are skipped, and a frame that sets `_rich_traceback_guard` clears the frames collected so far.
