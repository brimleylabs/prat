+++
id = "k0f1fa95a8f"
kind = "convention"
summary = "ConsoleOptions is immutable by convention; derive new ones with update*/copy"
paths = ["rich/console.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:16+00:00"
last_verified = "2026-11-13T05:41:16+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
`ConsoleOptions.update`, `update_width`, `update_height`, `update_dimensions` and `reset_height` each return a copy. They use the `NO_CHANGE` sentinel so that `None` can be a real value for justify, overflow, highlight and height. Renderables should call these methods rather than mutate the options they receive.
