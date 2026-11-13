+++
id = "kc7c1eafda5"
kind = "convention"
summary = "Pre-commit checklist: tests, mypy, black, with full type annotations"
paths = ["CONTRIBUTING.md", ".pre-commit-config.yaml"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:10+00:00"
last_verified = "2026-11-13T05:38:10+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
CONTRIBUTING.md asks for tests, type checking and black formatting before each commit. It also asks for docstrings on new code, type annotations on all new code, and no abbreviations in names. Pre-commit runs black through `uv run --frozen black`, plus isort (profile black), pycln and the standard hooks, and it excludes `benchmarks/`.
