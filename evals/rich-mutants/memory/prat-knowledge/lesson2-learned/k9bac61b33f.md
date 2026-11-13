+++
id = "k9bac61b33f"
kind = "command"
summary = "PRAT gate: python -m pytest -q -p no:cacheprovider tests"
paths = [".prat/project.toml", "pyproject.toml"]
status = "superseded"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:47+00:00"
last_verified = "2026-11-13T12:20:41+00:00"
invalid_at = "2026-11-13T12:21:14+00:00"
superseded_by = "kccca5722d6"

[[sources]]
note = "deep scan, 20261113T121106"
+++
.prat/project.toml defines a single 'test' gate that every card must pass, with a 300-second timeout. It runs about 1,000 tests in a few seconds. pyproject.toml sets testpaths to tests.
