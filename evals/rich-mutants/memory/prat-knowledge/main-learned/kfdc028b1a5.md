+++
id = "kfdc028b1a5"
kind = "pitfall"
summary = "Traceback has a Trace/Stack/Frame data model and a few quirks"
paths = ["rich/traceback.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:23+00:00"
last_verified = "2026-11-13T05:38:23+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
`Traceback.extract` builds `Trace`/`Stack`/`Frame` data and follows `__cause__` and `__context__`. It also handles ExceptionGroup on Python 3.11 and later, using `co_positions` for the error range. Frames are skipped when `_rich_traceback_omit` is set in the locals, and `_rich_traceback_guard` clears earlier frames. The constructor parameter `locals_overlow` is misspelled, and `from_exception` and `install` take `locals_overflow` and map it to it. `extract` does not pass `locals_max_string` or `locals_max_depth` to nested group exceptions.
