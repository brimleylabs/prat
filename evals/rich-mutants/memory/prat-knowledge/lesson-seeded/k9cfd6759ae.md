+++
id = "k9cfd6759ae"
kind = "layout"
summary = "Dependency layering: Segment/Style/cells at the bottom, Text next, then Table/Panel/Tree/Syntax/Progress on top"
paths = ["rich/segment.py", "rich/style.py", "rich/cells.py", "rich/text.py", "rich/table.py", "rich/panel.py", "rich/_unicode_data"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:24+00:00"
last_verified = "2026-11-13T05:41:24+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
segment.py, style.py and cells.py (backed by rich/_unicode_data) are the primitives. text.py builds on them, and almost every higher renderable renders through Text. Changes at the bottom ripple through the entire library and test suite. Changes to top-level renderables stay local.
