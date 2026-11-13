+++
id = "k2e29fbf5f4"
kind = "layout"
summary = "box.rst embeds an SVG produced by `python -m rich.box`"
paths = ["docs/source/appendix/box.rst", "docs/images"]
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
The page includes `../../images/box.svg`, which resolves to docs/images/box.svg. That image is the output of `python -m rich.box`. Update the SVG when box styles change in rich/box.py. The page also carries a note about Windows legacy terminals and the `safe_box` Table parameter.
