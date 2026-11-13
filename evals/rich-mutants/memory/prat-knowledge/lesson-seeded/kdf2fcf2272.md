+++
id = "kdf2fcf2272"
kind = "layout"
summary = "examples: The examples directory holds 36 standalone demo scripts and a short README for Rich"
paths = ["examples"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:13+00:00"
last_verified = "2026-11-13T05:41:13+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
The examples directory holds 36 standalone demo scripts and a short README for Rich. Each script shows one feature: tables, progress bars, Live displays, Layout, tracebacks, highlighters, console export, status spinners and others. They are not a package and have no tests of their own. Each one imports the library from the repo-level `rich/` package (Console, Live, Progress, Table, Layout, Panel, Tree, Text and so on), so they work as manual smoke tests of the public API. Run any of them with `python examples/<name>.py`, as `examples/README.md` says. Some scripts need extra packages or network access, and several write files into the current directory.
