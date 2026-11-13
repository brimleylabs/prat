+++
id = "ke94cf8d3e0"
kind = "layout"
summary = "Import path and config live outside the module"
paths = ["benchmarks/__init__.py", "benchmarks/README.md", "benchmarks/benchmarks.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:13+00:00"
last_verified = "2026-11-13T05:38:13+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
`benchmarks.py` does `from benchmarks import snippets`, which is why `benchmarks/__init__.py` exists (it is empty). The asv config `asv.conf.json` and the `asvhashfile` are at the repo root. The README references them, but they are not in this module.
