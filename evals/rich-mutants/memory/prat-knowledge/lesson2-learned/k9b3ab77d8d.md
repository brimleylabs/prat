+++
id = "k9b3ab77d8d"
kind = "layout"
summary = "File name matches module name; the top-level package is init.rst"
paths = ["docs/source/reference/init.rst", "docs/source/reference/progress_bar.rst"]
status = "superseded"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:41+00:00"
last_verified = "2026-11-13T12:20:41+00:00"
invalid_at = "2026-11-13T12:21:15+00:00"
superseded_by = "k193b2c8b57"

[[sources]]
note = "deep scan, 20261113T121106"
+++
Each page is named after its module, for example `rich/progress_bar.py` is documented in `progress_bar.rst`. The one exception is `init.rst`, which documents the `rich` package itself with `.. automodule:: rich`. Many modules in `rich/` have no page here, and I did not check which ones.
