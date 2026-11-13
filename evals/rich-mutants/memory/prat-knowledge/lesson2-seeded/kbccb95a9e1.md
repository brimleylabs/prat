+++
id = "kbccb95a9e1"
kind = "pitfall"
summary = "asv benchmark config is stale relative to the build system"
paths = ["asv.conf.json", "pyproject.toml"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:38+00:00"
last_verified = "2026-11-13T12:13:38+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
`asv.conf.json` has `build_command` entries that run `pip install poetry` and `python setup.py build`. It also pins `setuptools 59.2.0` in the matrix, tracks only the `master` branch and uses Python 3.10. The project now builds with `uv_build`, and the default branch here is `main`. Benchmark output goes to `./benchmarks/results`, with html in `./benchmarks/html` and environments in `./benchmarks/env`. Don't assume `asv run` works unchanged.
