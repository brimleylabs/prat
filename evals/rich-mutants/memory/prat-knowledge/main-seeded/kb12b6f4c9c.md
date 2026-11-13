+++
id = "kb12b6f4c9c"
kind = "layout"
summary = "tools: The tools module is a small set of developer-only scripts for Rich, and tools/README.md says they are not for general use"
paths = ["tools"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:26+00:00"
last_verified = "2026-11-13T05:38:26+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
The tools module is a small set of developer-only scripts for Rich, and tools/README.md says they are not for general use. It contains a generator for the Unicode cell-width tables, a generator for the emoji code table, two profiling scripts, a stress test for Pretty, and a sample Markdown file. The width-table generator writes into rich/_unicode_data/ and uses rich.cells.CellTable. The profiling and stress scripts import rich.segment, rich.console, rich.pretty, rich.panel and rich._timer. None of the scripts is imported by the library, and the module has no test runner or CLI wrapper.
