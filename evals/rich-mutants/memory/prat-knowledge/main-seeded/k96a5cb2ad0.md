+++
id = "k96a5cb2ad0"
kind = "pitfall"
summary = "The two files come from different Rich export versions"
paths = ["docs/images/box.svg", "docs/images/svg_export.svg"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:15+00:00"
last_verified = "2026-11-13T05:38:15+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
box.svg uses font-variant-east-asian: full-width and a line-height of 24.4px, and its frame is #292929 with rounded corners. svg_export.svg uses `font-variant: east-asian-width-values`, a line-height of 26.4px and a #191919 background. Regenerating one file with the current exporter will not make it match the other, so check how both look in the built docs.
