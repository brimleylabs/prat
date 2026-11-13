+++
id = "kbb7d4ac99d"
kind = "pitfall"
summary = "Page content depends on docstrings in rich/, and the module path must be importable"
paths = ["docs/source/reference/console.rst", "docs/source/reference/segment.rst"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:41+00:00"
last_verified = "2026-11-13T12:13:41+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
The pages contain no prose, so API documentation changes happen as docstring edits in the matching `rich/*.py` file. Renaming or removing a module or a listed member without updating the stub causes Sphinx import or member warnings.
