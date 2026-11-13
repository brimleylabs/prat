+++
id = "ka2b886b2dd"
kind = "convention"
summary = "Appendix pages begin with an explicit reStructuredText label"
paths = ["docs/source/appendix/box.rst", "docs/source/appendix/colors.rst"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:14+00:00"
last_verified = "2026-11-13T05:38:14+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
box.rst starts with `.. _appendix_box:` and colors.rst with `.. _appendix-colors:`. The two labels use different separators, underscore versus hyphen. Other pages may link to them by these exact names, so check for references with Grep before renaming either one.
