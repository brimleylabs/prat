+++
id = "k1e5bcb71c2"
kind = "pitfall"
summary = "`reconfigure()` swaps the global console's `__dict__` instead of rebinding it"
paths = ["rich/__init__.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:42+00:00"
last_verified = "2026-11-13T12:13:42+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
`reconfigure` builds a new `Console` and copies its `__dict__` onto the existing global instance (`_console.__dict__ = new_console.__dict__`). Modules that already hold a reference to the global console see the new settings. Replacing this with a plain rebind of `_console` would leave those holders on the old config.
