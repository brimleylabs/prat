+++
id = "kccca5722d6"
kind = "command"
summary = "The single verification gate is the pytest run defined in .prat/project.toml; run it before finishing any change."
paths = [".prat/project.toml", "pyproject.toml"]
status = "active"
confidence = "confirmed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:37+00:00"
last_verified = "2026-11-13T12:13:37+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"

[[sources]]
note = "deep scan, 20261113T121106"
+++
`.prat/project.toml` defines `[commands].test` as `{python} -m pytest -q -p no:cacheprovider tests`, and `[verify].gates = ["test"]` with `timeout_seconds = 300`, so every card must pass this one gate. It runs about 1,000 tests in a few seconds, and `pyproject.toml` sets testpaths to `tests`. Run the same command before finishing any change.
