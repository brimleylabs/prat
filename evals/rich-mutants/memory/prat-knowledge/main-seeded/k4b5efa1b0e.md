+++
id = "k4b5efa1b0e"
kind = "decision"
summary = "highlighter and style also document `__call__` through `:special-members:`."
paths = ["docs/source/reference/highlighter.rst", "docs/source/reference/style.rst"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:17+00:00"
last_verified = "2026-11-13T05:38:17+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
highlighter.rst and style.rst add `:special-members: __call__`, so the callable behaviour of Highlighter and Style appears in the docs. Keep that option if you rewrite those pages.
