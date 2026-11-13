+++
id = "k73d9b0eef0"
kind = "pitfall"
summary = "Title underline must be at least as long as the title"
paths = ["docs/source/reference/progress_bar.rst", "docs/source/reference/traceback.rst"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:41+00:00"
last_verified = "2026-11-13T12:20:41+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
reStructuredText warns when the underline is shorter than the title. A longer underline is accepted. A new page also has to be added to the toctree, which I did not see; check the docs/source index file.
