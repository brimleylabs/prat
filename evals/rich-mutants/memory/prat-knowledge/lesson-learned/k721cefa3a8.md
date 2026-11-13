+++
id = "k721cefa3a8"
kind = "layout"
summary = "tools: tools/ holds standalone developer scripts for Rich"
paths = ["tools"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:22+00:00"
last_verified = "2026-11-13T05:41:22+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
tools/ holds standalone developer scripts for Rich. They are not shipped and not for general use, as tools/README.md says. They cover three jobs: generating data (emoji codes, Unicode width tables), profiling or stress-testing rendering (Segment.divide, Pretty), and a changelog check. They import from the rich package (rich.cells, rich.console, rich.panel, rich.pretty, rich.segment, rich._timer). make_width_tables.py writes into rich/_unicode_data/. tools/cats.json is the sample input for profile_pretty.py, and tools/movies.md is a sample Markdown document with tables.
