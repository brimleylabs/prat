+++
id = "kcd6ea0a834"
kind = "layout"
summary = "index.rst is the master toctree; a new page must be listed there to be built"
paths = ["docs/source/index.rst", "docs/source/reference.rst", "docs/source/appendix.rst"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:11+00:00"
last_verified = "2026-11-13T05:41:11+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
Guide pages are listed by hand in docs/source/index.rst, with the API pages under reference.rst and appendix.rst, which glob nothing and list each sub-file explicitly. A new feature page, or a new docs/source/reference/*.rst file, is not part of the build until it is added to the matching toctree. The paths named in reference.rst and appendix.rst must exist, for example reference/progress_bar.rst and appendix/box.rst.
