+++
id = "kafee1b6b90"
kind = "convention"
summary = "Before each commit: tests pass, mypy passes, black formatting, plus the pre-commit hooks"
paths = ["CONTRIBUTING.md", ".pre-commit-config.yaml"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:05+00:00"
last_verified = "2026-11-13T05:41:05+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
CONTRIBUTING.md asks for tests, type-checking and black before every commit. It also asks for docstrings on new code, type annotations on all new code, no abbreviations in names, and consistent API style. The pre-commit config adds check-ast, check-yaml/json/toml, end-of-file-fixer, trailing-whitespace, pycln (`--all`) and isort (`--profile black`). Black runs as a local hook via `uv run --frozen black`. The whole `benchmarks/` directory is excluded from the hooks.
