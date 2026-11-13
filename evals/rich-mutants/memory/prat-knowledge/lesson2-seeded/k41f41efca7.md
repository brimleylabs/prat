+++
id = "k41f41efca7"
kind = "convention"
summary = "Code samples use indented literal blocks introduced by '::' and show the public API (from rich import print, Console, and so on)."
paths = ["docs/source/live.rst", "docs/source/progress.rst", "docs/source/columns.rst", "docs/source/tree.rst", "docs/source/traceback.rst", "docs/source/pretty.rst"]
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
Examples are self-contained and import from rich.* modules. Pages usually mention the matching demo command (python -m rich.<module>), for instance python -m rich.live, rich.progress, rich.layout, rich.pretty, rich.traceback, rich.markdown and rich.syntax. They also link to files in the repo's examples/ directory through absolute github.com/willmcgugan/rich/blob/master/examples/... URLs.
