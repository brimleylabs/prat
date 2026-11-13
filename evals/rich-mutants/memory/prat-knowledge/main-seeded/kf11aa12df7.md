+++
id = "kf11aa12df7"
kind = "pitfall"
summary = "Progress mixes a background Live refresh thread with a shared RLock, and the Task fields are read-only to callers"
paths = ["rich/progress.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:21+00:00"
last_verified = "2026-11-13T05:38:21+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
`Progress` creates a `Live` with `get_renderable=self.get_renderable`. It guards `_tasks` and each `Task._lock` with a single `RLock`, which `add_task` shares with the `Task`. `track()` starts a daemon `_TrackThread` when `auto_refresh` is on, and otherwise advances and refreshes in the loop. `update()` resets a task's speed samples when `total` changes. `advance()` records `finished_speed` and `update()` does not, so the two paths are not symmetric. `Task` is documented as read-only outside `Progress`.
