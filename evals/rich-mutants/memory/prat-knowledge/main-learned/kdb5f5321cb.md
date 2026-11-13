+++
id = "kdb5f5321cb"
kind = "stack"
summary = "Python: CI matrix 3.10-3.15 (windows-latest skips 3.13); .python-version is 3.15; pyproject requires >=3.10."
paths = [".github/workflows/pythonpackage.yml", ".python-version", "pyproject.toml"]
status = "active"
confidence = "observed"
author = "checker"
helped = 0
hurt = 0
created = "2026-11-13T05:47:32+00:00"
last_verified = "2026-11-13T05:47:32+00:00"
invalid_at = ""
superseded_by = ""

[[links]]
to = "k2123c44fc0"
relation = "refines"

[[sources]]
note = "deep scan, 20261113T053544"

[[sources]]
note = "checked against aa3a3c47b423"
+++
.github/workflows/pythonpackage.yml runs on pull_request across windows, ubuntu and macos with Python 3.10, 3.11, 3.12, 3.13, 3.14 and 3.15 (Windows excludes 3.13). .python-version pins 3.15 and pyproject.toml declares requires-python >=3.10. CI installs with uv sync --locked.
