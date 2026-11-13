+++
id = "k90a8fca7de"
kind = "pitfall"
summary = "Do not hand-edit the SVGs; regenerate them from Rich"
paths = ["docs/images/box.svg", "docs/images/svg_export.svg"]
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
Every text run has an absolute x/y position and a `textLength`. Colours and styles are indirected through generated classes: `terminal-2816723390-r1..r5` in box.svg and `terminal-2501951896-r1..r442` in svg_export.svg. Changing one string or colour by hand will misalign the layout or the class mapping. Regenerate instead; I did not find a script for this in these files.
