+++
id = "ke86b3e81da"
kind = "decision"
summary = "Packaging moved from Poetry to uv (uv_build); Python >=3.10 required, 3.15 added, 3.9 dropped"
paths = ["pyproject.toml", ".python-version", "CHANGELOG.md", ".github/workflows/pythonpackage.yml", ".pre-commit-config.yaml"]
status = "active"
confidence = "confirmed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:48+00:00"
last_verified = "2026-11-13T12:13:48+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"

[[sources]]
note = "deep scan, 20261113T121106"
+++
The CHANGELOG 'Unreleased' section records the switch from Poetry to uv_build, dropping Python 3.9 and adding 3.15. pyproject.toml requires >=3.10, `.python-version` pins 3.15, CI covers 3.10–3.15, and the pre-commit black hook runs `uv run --frozen black`. Code must stay compatible with 3.10 syntax; the last release in the CHANGELOG is 15.0.0 (2026-04-12).
