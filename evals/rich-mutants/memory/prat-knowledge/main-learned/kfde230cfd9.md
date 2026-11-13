+++
id = "kfde230cfd9"
kind = "convention"
summary = "Text sanitises control codes and uses a fixed set of style-merge methods"
paths = ["rich/text.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:22+00:00"
last_verified = "2026-11-13T05:38:22+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
The constructor, the `plain` setter, `append` and `append_tokens` all call `strip_control_codes` on incoming strings. `append(Text, style=...)` raises ValueError if a style is passed with a Text. `stylize` adds a span at the end, `stylize_before` inserts at index 0, and later spans win when `render` combines styles in span order. `__slots__` is used, so a new attribute must be added to the list.
