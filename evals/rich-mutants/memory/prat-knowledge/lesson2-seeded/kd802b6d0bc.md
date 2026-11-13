+++
id = "kd802b6d0bc"
kind = "layout"
summary = "Rich is a single library package rich/ with tests/, docs/, examples/, benchmarks/ and tools/ around it"
paths = ["rich", "tests", "docs/source", "examples", "benchmarks", "tools", "pyproject.toml"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:47+00:00"
last_verified = "2026-11-13T12:13:47+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
All shipped code is in rich/ at the repo root, built with uv_build. tests/ is the pytest suite and docs/ is the Sphinx site. examples/, benchmarks/ (asv) and tools/ are developer-only, and nothing in the library imports them.
