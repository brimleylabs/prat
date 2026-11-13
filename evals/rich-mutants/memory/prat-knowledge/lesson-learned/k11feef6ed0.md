+++
id = "k11feef6ed0"
kind = "pitfall"
summary = "Console output buffering is thread-local and nested; Live relies on it"
paths = ["rich/console.py", "rich/live.py", "rich/file_proxy.py", "rich/progress.py"]
status = "superseded"
confidence = "observed"
author = "scout"
helped = 1
hurt = 0
created = "2026-11-13T05:41:25+00:00"
last_verified = "2026-11-13T06:49:48+00:00"
invalid_at = "2026-11-13T06:50:32+00:00"
superseded_by = "k12f254e568"

[[sources]]
note = "deep scan, 20261113T053836"
+++
Every write path must go through `with self:` / _check_buffer. Live and Progress install render hooks, push onto a live stack and redirect stdout/stderr through FileProxy. Progress tasks may only be mutated under the lock.
