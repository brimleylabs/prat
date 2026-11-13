+++
id = "k232d9bfaed"
kind = "layout"
summary = "examples: The examples/ directory holds 36 standalone demo scripts, plus a README, that show off Rich features"
paths = ["examples"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:17+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
The examples/ directory holds 36 standalone demo scripts, plus a README, that show off Rich features. Each script is run directly with `python <name>.py`. They cover tables, panels, columns, trees, layouts, Live displays, progress bars, status spinners, logging, tracebacks, highlighters, `rich.repr`, and console export to text, HTML and SVG. They are written against the public API in rich/ (`rich.console`, `rich.live`, `rich.progress`, `rich.table`, `rich.layout` and others). They are documentation by example and are not packaged or tested as part of the library. A few need third-party packages (attrs, click) or network access (columns.py, file_progress.py, downloader.py).
