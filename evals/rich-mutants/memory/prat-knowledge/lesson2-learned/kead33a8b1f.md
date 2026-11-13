+++
id = "kead33a8b1f"
kind = "convention"
summary = "Code style: mypy strict on rich/, black and isort, docstrings, full names and type annotations"
paths = ["pyproject.toml", ".pre-commit-config.yaml", "CONTRIBUTING.md"]
status = "active"
confidence = "confirmed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:47+00:00"
last_verified = "2026-11-13T12:13:47+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"

[[sources]]
note = "deep scan, 20261113T121106"
+++
CONTRIBUTING.md asks for docstrings on new code, no abbreviations in names, consistent API design, and type annotations on all new code. pyproject.toml enables mypy strict for the rich package only (tests are not type-checked) and configures isort. Pre-commit runs black via uv, isort with `--profile black`, pycln `--all`, and pygrep hooks, excluding `benchmarks/`; CI also enforces black.
