+++
id = "k7ad0ba81a1"
kind = "layout"
summary = "box.rst embeds an SVG that lives outside docs/source"
paths = ["docs/source/appendix/box.rst", "docs/images"]
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
`box.rst` uses `.. image:: ../../images/box.svg`, which resolves to `docs/images/box.svg`. The page says the image was generated with `python -m rich.box`. If you change the box styles, regenerate the SVG and keep that relative path working.
