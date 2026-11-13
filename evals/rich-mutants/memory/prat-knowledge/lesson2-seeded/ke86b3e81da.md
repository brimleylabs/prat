+++
id = "ke86b3e81da"
kind = "decision"
summary = "Packaging moved from Poetry to uv; Python >=3.10, with 3.15 in the matrix"
paths = ["pyproject.toml", ".python-version", "CHANGELOG.md", ".github/workflows/pythonpackage.yml"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:48+00:00"
last_verified = "2026-11-13T12:13:48+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
CHANGELOG records the move to uv_build and the dropping of 3.9. pyproject.toml requires >=3.10, .python-version says 3.15, and CI covers 3.10–3.15. Code must stay compatible with 3.10 syntax.
