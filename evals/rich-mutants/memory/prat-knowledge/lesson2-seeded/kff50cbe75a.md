+++
id = "kff50cbe75a"
kind = "pitfall"
summary = "Console and layout output screenshots are hand-written raw HTML <pre> blocks and do not update when behaviour changes."
paths = ["docs/source/console.rst", "docs/source/introduction.rst", "docs/source/layout.rst", "docs/source/tables.rst"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:40+00:00"
last_verified = "2026-11-13T12:13:40+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
console.rst, introduction.rst, layout.rst and tables.rst embed `.. raw:: html` blocks with inline-styled spans to show rendered output. If the library's output or defaults change, these blocks must be edited by hand. They are also whitespace-sensitive, so keep the indentation of the literal output when editing.
