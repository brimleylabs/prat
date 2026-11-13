+++
id = "k8a2db31bbe"
kind = "convention"
summary = "Each suite is a class with setup() that builds a Console on StringIO with fixed settings, plus time_* methods."
paths = ["benchmarks/benchmarks.py", "benchmarks/__init__.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:39+00:00"
last_verified = "2026-11-13T12:13:39+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
Consoles use `Console(file=StringIO(), color_system="truecolor", legacy_windows=False)`, and some also set `width=100`. This keeps output deterministic and off the real terminal. Inputs come from `from benchmarks import snippets`, so the import depends on running from the repo root with `benchmarks/__init__.py` present (it is empty). Shared helpers are underscore methods such as `_print_with_width` and `_print_table`.
