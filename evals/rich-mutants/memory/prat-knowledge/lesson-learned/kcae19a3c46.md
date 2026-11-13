+++
id = "kcae19a3c46"
kind = "decision"
summary = "Python support moved to uv: 3.10+ supported, .python-version pins 3.15, Poetry removed"
paths = [".python-version", ".readthedocs.yml", "CHANGELOG.md", "CONTRIBUTING.md"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:06+00:00"
last_verified = "2026-11-13T05:41:06+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
The Unreleased section of the changelog records dropping Python 3.9, adding 3.15 and switching from Poetry to uv. `.python-version` contains `3.15`, which `uv sync` downloads. Read the Docs builds on Python 3.12 (ubuntu-24.04) from docs/source/conf.py and installs docs/requirements.txt plus the package. When changing supported versions, check the CHANGELOG, `.python-version`, `.readthedocs.yml` and pyproject.toml together.
