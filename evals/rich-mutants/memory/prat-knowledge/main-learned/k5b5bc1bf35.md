+++
id = "k5b5bc1bf35"
kind = "pitfall"
summary = "Hand edits break the numeric layout and the hashed class names"
paths = ["docs/images/box.svg", "docs/images/svg_export.svg"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:15+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
Every text run has absolute x/y coordinates and a `textLength`, and each line has its own clip-path. Changing a character or its width misaligns the box drawing. The hash in the class and id names (2816723390 in box.svg, 2501951896 in svg_export.svg) is part of every selector, so partial renames or find-and-replace edits break the styling.
