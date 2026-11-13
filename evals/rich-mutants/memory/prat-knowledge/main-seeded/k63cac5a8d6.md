+++
id = "k63cac5a8d6"
kind = "layout"
summary = "init.rst documents the top-level `rich` package, and its filename doesn't match the module."
paths = ["docs/source/reference/init.rst"]
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
docs/source/reference/init.rst has the title `rich` and uses `automodule:: rich`, so it covers rich/__init__.py. Its filename is `init`, not `rich`. Its `:members:` line is followed by a line of trailing whitespace.
