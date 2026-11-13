+++
id = "kd5d590bfc9"
kind = "convention"
summary = "Sample terminal output is embedded as hand-written `.. raw:: html` <pre> blocks, not generated"
paths = ["docs/source/console.rst", "docs/source/tables.rst", "docs/source/layout.rst", "docs/source/introduction.rst"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:12+00:00"
last_verified = "2026-11-13T05:41:12+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
Screenshots of rendered output (for example the log line and rule in console.rst, the table in tables.rst, the layout boxes in layout.rst, and the locals() dump in introduction.rst) are static inline HTML with colour spans and a monospace font-family. They do not update when rendering code changes, so a behaviour change in rich/ may need a manual edit here. Code samples use `::` literal blocks indented four spaces. Doctest-style `>>>` snippets are used for REPL-style examples.
