+++
id = "kb99aa7a2c7"
kind = "convention"
summary = "Code is formatted with black, and pre-commit enforces it"
paths = [".pre-commit-config.yaml", "Makefile"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:28+00:00"
last_verified = "2026-11-13T05:38:28+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
Run `make format` before you commit. CI fails on `black --check .`, which covers the whole repo including tests and tools.
