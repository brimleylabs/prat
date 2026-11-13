+++
id = "k12f3e2cc2f"
kind = "pitfall"
summary = "`rich.inspect()` and `Inspect()` have different defaults for `all`"
paths = ["rich/__init__.py", "rich/_inspect.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:42+00:00"
last_verified = "2026-11-13T12:20:41+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
The public `inspect()` in `rich/__init__.py` defaults `all=False`. The `Inspect` class in `rich/_inspect.py` defaults `all=True`, and `all=True` forces `methods`, `private` and `dunder` on. Constructing `Inspect` directly therefore shows far more than calling `rich.inspect`.
