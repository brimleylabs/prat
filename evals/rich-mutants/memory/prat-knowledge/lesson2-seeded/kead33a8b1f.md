+++
id = "kead33a8b1f"
kind = "convention"
summary = "mypy strict on rich/, black and isort formatting, full type annotations"
paths = ["pyproject.toml", ".pre-commit-config.yaml", "CONTRIBUTING.md"]
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
pyproject.toml enables mypy strict for the rich package only, and tests are not type-checked. CONTRIBUTING.md asks for docstrings, full names and type annotations. Pre-commit and CI enforce black, and isort is configured in pyproject.toml.
