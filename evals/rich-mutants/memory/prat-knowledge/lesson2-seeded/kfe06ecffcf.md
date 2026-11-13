+++
id = "kfe06ecffcf"
kind = "pitfall"
summary = "Progress is thread-locked, and Task and column caching have rules"
paths = ["rich/progress.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:44+00:00"
last_verified = "2026-11-13T12:13:44+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
`Progress._lock` is an `RLock` shared with each `Task` (`_lock=self._lock`), and `update`, `advance`, `reset` and `add_task` all mutate task state under it. `ProgressColumn.__call__` caches renderables per task id when `max_refresh` is set. `TimeRemainingColumn` sets it to 0.5 and skips the cache once `task.completed` is nonzero. `Task._progress` is a `deque(maxlen=1000)` of `ProgressSample`, pruned by `speed_estimate_period`. `Progress.track` uses a `_TrackThread` only when `live.auto_refresh` is on. When it is off it advances and refreshes manually.
