+++
id = "kdf3647a9fd"
kind = "layout"
summary = "snippets.py holds shared fixed test data"
paths = ["benchmarks/snippets.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:09+00:00"
last_verified = "2026-11-13T05:41:09+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
`PYTHON_SNIPPET`, `PYTHON_DICT`, `LOREM_IPSUM`, `UNICODE_HEAVY_TEXT` (Japanese README text) and `MARKUP` (a `[bold]...[/]` line repeated 20 times) are module-level constants. Changing them alters every benchmark that uses them and breaks comparability with earlier results. The unicode text is deliberately full-width, to exercise cell-width handling.
