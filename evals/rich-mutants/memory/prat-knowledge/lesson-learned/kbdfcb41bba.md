+++
id = "kbdfcb41bba"
kind = "layout"
summary = "Appendix pages are registered in appendix.rst and sit under docs/source/appendix/"
paths = ["docs/source/appendix.rst", "docs/source/appendix/box.rst", "docs/source/appendix/colors.rst"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:10+00:00"
last_verified = "2026-11-13T05:41:10+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
docs/source/appendix.rst has a toctree that lists appendix/box.rst and appendix/colors.rst. A new appendix page must be added to that toctree. Otherwise Sphinx treats it as an orphan page and warns about it.
