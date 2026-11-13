+++
id = "kece28c5ba3"
kind = "pitfall"
summary = "Box, Style and Text have strict internal invariants"
paths = ["rich/box.py", "rich/style.py", "rich/text.py", "rich/table.py", "rich/_ratio.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 1
hurt = 0
created = "2026-11-13T05:38:30+00:00"
last_verified = "2026-11-13T05:38:30+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
A Box must be exactly 8 lines of 4 characters. Style is immutable, uses __slots__ and caches heavily, so every constructor path must stay in sync. Text spans are character offsets over lazily joined string parts. Table widths are computed in several interdependent passes using the _ratio helpers.
