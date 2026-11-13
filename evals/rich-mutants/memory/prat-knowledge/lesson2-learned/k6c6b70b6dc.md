+++
id = "k6c6b70b6dc"
kind = "pitfall"
summary = "Output buffering, env-var terminal detection and Live/FileProxy are fragile"
paths = ["rich/console.py", "rich/live.py", "rich/file_proxy.py", "rich/live_render.py", "Makefile"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:48+00:00"
last_verified = "2026-11-13T12:13:48+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
Console writes reach the file only when the buffer index returns to 0. Color and terminal detection reads many env vars (TERM, FORCE_COLOR, NO_COLOR, COLUMNS and others), which tests inject through _environ. Make runs tests with TERM=unknown. Live redirects stdout through FileProxy and controls the cursor, and nesting it is easy to break.
