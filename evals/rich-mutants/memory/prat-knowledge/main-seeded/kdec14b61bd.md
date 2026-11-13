+++
id = "kdec14b61bd"
kind = "convention"
summary = "Progress columns subclass ProgressColumn and implement render(task)"
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
A column subclasses `ProgressColumn`, implements `render(task)` and passes an optional `table_column` to `super().__init__`. A class-level `max_refresh` (for example 0.5 in `TimeRemainingColumn`) caches the renderable per task id. The cache is skipped when `task.completed` is falsy. `track`, `wrap_file` and `open` each build their own column list, with the description `TextColumn` added only if a description is given. Their defaults are repeated across the module-level functions, so a change to one often needs the others updated.
