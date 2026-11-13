+++
id = "ked20ce945d"
kind = "layout"
summary = "Docs build config and ignored paths"
paths = [".readthedocs.yml", ".gitignore", "README.cn.md"]
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
Read the Docs builds `docs/source/conf.py` on ubuntu-24.04 with Python 3.12, installing `docs/requirements.txt` and the package, with `formats: all`. `.gitignore` excludes `docs/build`, `mypy_report`, `.venv`, `playground/`, `sandbox/`, `benchmarks/env/`, `benchmarks/html/` and `tools/*.txt`. The README translations in this module are linked to each other by a language list at the top of each file.
